"""The ASVA health center - one snapshot of everything, and what needs attention.

``build_health(db)`` assembles a single picture on a FIXED handful of queries so
it stays cheap as shops grow: every business's online/WhatsApp/Tally state, how
many messages went out / failed / are queued today, 14-day traffic, the stuck
queue, and the scheduler's own job heartbeats.

``evaluate(health)`` turns that picture into a list of problems (server/bot/shop
WhatsApp down, offline agents, stuck queues, high failure rates) which the
watchdog (jobs/monitor.py) emails you about via services/alerts.reconcile.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict

from app.config import settings
from app.models import PLAN_LABELS, PLAN_LIMITS, Plan
from app.services import subscription as subs

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

SENT = {"sent", "delivered", "read"}
DROP = {"failed", "expired"}
BLOCKED = {"limit_reached", "subscription_suspended"}
QUEUED_ST = {"queued"}

# A job is "stale" if it has not run in this many multiples of its own cadence.
_JOB_MAX_QUIET_MIN = {
    "reminder_sweep": 130,       # hourly -> quiet > ~2h = wrong
    "eod_digest": 130,           # hourly
    "subscription_check": 1500,  # daily -> quiet > ~25h
    "monitor": 20,               # every few min
    "outbox_sweep": 8,           # every minute
}


def _paged(query_fn, size: int = 1000) -> list:
    rows, start = [], 0
    while True:
        batch = query_fn().range(start, start + size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < size:
            return rows
        start += size


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _mins_since(dt, now) -> int:
    if not dt:
        return 10 ** 9
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int((now - dt).total_seconds() // 60)


def _plan(biz: dict) -> Plan:
    try:
        return Plan(biz.get("plan") or "starter")
    except ValueError:
        return Plan.starter


def stamp_job(db, name: str, ok: bool = True, detail: str | None = None) -> None:
    """Record that a scheduler job just ran (for the health center's job list +
    'a job stopped running' alert). Best-effort - never fails the job over it."""
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        db.table("job_heartbeats").upsert({
            "job_name": name, "last_run_at": now, "ok": ok,
            "detail": (detail or "")[:300], "updated_at": now,
        }).execute()
    except Exception:
        pass


def build_health(db) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    today_ist = _dt.datetime.now(IST).date()
    day_start = _dt.datetime.combine(today_ist, _dt.time.min, tzinfo=IST).astimezone(_dt.timezone.utc)
    span_start = day_start - _dt.timedelta(days=13)   # 14-day window incl today

    bizes = _paged(lambda: db.table("businesses").select(
        "id, business_name, plan, plan_expires_on, last_seen, agent_version, "
        "whatsapp_number, wa_ready, wa_checked_at, outbox_pending"))
    biz_by_id = {b["id"]: b for b in bizes}

    # Today's messages -> per business + totals, bucketed by outcome.
    sent_t = Counter(); drop_t = Counter(); block_t = Counter(); queued_t = Counter()
    for m in _paged(lambda: db.table("messages").select("business_id, delivery_status")
                    .gte("created_at", day_start.isoformat())):
        bid, st = m.get("business_id"), m.get("delivery_status")
        if st in SENT: sent_t[bid] += 1
        elif st in DROP: drop_t[bid] += 1
        elif st in BLOCKED: block_t[bid] += 1
        elif st in QUEUED_ST: queued_t[bid] += 1

    # 14-day traffic (all shops) -> daily sent / failed for the sparkline.
    daily = defaultdict(lambda: {"sent": 0, "failed": 0, "blocked": 0})
    for m in _paged(lambda: db.table("messages").select("created_at, delivery_status")
                    .gte("created_at", span_start.isoformat())):
        d = _parse_ts(m.get("created_at"))
        if not d:
            continue
        key = d.astimezone(IST).date().isoformat()
        st = m.get("delivery_status")
        if st in SENT: daily[key]["sent"] += 1
        elif st in DROP: daily[key]["failed"] += 1
        elif st in BLOCKED: daily[key]["blocked"] += 1
    traffic = [{"date": (span_start.astimezone(IST).date() + _dt.timedelta(days=i)).isoformat(),
                **daily[(span_start.astimezone(IST).date() + _dt.timedelta(days=i)).isoformat()]}
               for i in range(14)]

    # Stuck queue: queued outbox rows, per business + oldest age.
    q_count = Counter(); q_oldest = {}
    for r in _paged(lambda: db.table("wa_outbox").select("business_id, created_at")
                    .eq("status", "queued")):
        bid = r.get("business_id")
        q_count[bid] += 1
        age = _mins_since(_parse_ts(r.get("created_at")), now)
        q_oldest[bid] = max(q_oldest.get(bid, 0), age)

    # Scheduler job heartbeats.
    jobs = []
    for j in (db.table("job_heartbeats").select("*").execute().data or []):
        mins = _mins_since(_parse_ts(j.get("last_run_at")), now)
        limit = _JOB_MAX_QUIET_MIN.get(j["job_name"], 1500)
        jobs.append({"name": j["job_name"], "mins_ago": mins, "ok": bool(j.get("ok")),
                     "stale": mins > limit, "detail": j.get("detail")})

    # Per-business health rows.
    rows = []
    tot = Counter()
    for b in bizes:
        bid = b["id"]
        status = subs.effective_status(b.get("plan_expires_on"), today_ist)
        online_min = _mins_since(_parse_ts(b.get("last_seen")), now)
        online = online_min <= settings.offline_alert_min
        wa_min = _mins_since(_parse_ts(b.get("wa_checked_at")), now)
        wa_ready = b.get("wa_ready")
        rows.append({
            "id": bid, "name": b.get("business_name") or "(unnamed)",
            "status": status, "online": online, "last_seen_min": online_min,
            "wa_ready": wa_ready, "wa_stale": wa_min > (settings.wa_down_alert_min * 4),
            "sent_today": sent_t.get(bid, 0), "failed_today": drop_t.get(bid, 0),
            "blocked_today": block_t.get(bid, 0),
            "queued": q_count.get(bid, 0), "queue_oldest_min": q_oldest.get(bid, 0),
        })
        tot["businesses"] += 1
        tot["online"] += 1 if online else 0
        tot[status] += 1
        tot["sent_today"] += sent_t.get(bid, 0)
        tot["failed_today"] += drop_t.get(bid, 0)
        tot["blocked_today"] += block_t.get(bid, 0)
        tot["queued_now"] += q_count.get(bid, 0)
        if wa_ready is False and status != "suspended":
            tot["wa_down"] += 1

    rows.sort(key=lambda r: (r["status"] != "suspended", r["online"], -r["failed_today"]))
    return {
        "generated_at": now.isoformat(),
        "server_version": settings.app_version,
        "totals": dict(tot),
        "traffic": traffic,
        "jobs": jobs,
        "businesses": rows,
        "biz_by_id": {k: v.get("business_name") for k, v in biz_by_id.items()},
    }


def evaluate(health: dict) -> list[dict]:
    """Turn the snapshot into current problems for alerts.reconcile. `health`
    may carry health['system']['bot_wa'] = {'ok': bool} injected by the job."""
    problems: list[dict] = []
    sysd = health.get("system", {})
    bot = sysd.get("bot_wa")
    if bot is not None and not bot.get("ok"):
        problems.append({"kind": "bot_wa_down", "severity": "critical",
                         "title": "Bot WhatsApp is disconnected",
                         "body": "The ASVA bot number on the host is not connected. "
                                 "Owner digests, alerts and replies are down. "
                                 "Open link.tryasva.com/qr and re-link if needed."})

    for j in health.get("jobs", []):
        if j.get("stale"):
            problems.append({"kind": f"job_stale:{j['name']}", "severity": "critical",
                             "title": f"Scheduler job '{j['name']}' stopped running",
                             "body": f"Last run {j['mins_ago']} min ago. The backend "
                                     f"scheduler may be stuck - check the host."})

    for r in health.get("businesses", []):
        name = r["name"]
        if r["status"] == "suspended":
            continue                                   # expected, not an incident
        if not r["online"]:
            problems.append({"kind": "shop_offline", "business_id": r["id"], "severity": "warn",
                             "title": f"{name}: agent offline",
                             "body": f"No contact for {r['last_seen_min']} min. Tally push and "
                                     f"queued sends are paused until the shop laptop is back on."})
        if r.get("wa_ready") is False:
            problems.append({"kind": "wa_down", "business_id": r["id"], "severity": "critical",
                             "title": f"{name}: WhatsApp disconnected",
                             "body": "The shop's WhatsApp is not connected, so bills and "
                                     "reminders cannot go out. Have the shopkeeper re-scan "
                                     "at localhost:3001/qr on the shop laptop."})
        if r["queued"] >= settings.outbox_backlog_alert:
            problems.append({"kind": "outbox_stuck", "business_id": r["id"], "severity": "warn",
                             "title": f"{name}: {r['queued']} messages stuck in queue",
                             "body": f"Oldest is {r['queue_oldest_min']} min old. The shop's "
                                     f"WhatsApp is likely down or the shop laptop is off."})
        attempted = r["sent_today"] + r["failed_today"]
        if attempted >= 10 and (r["failed_today"] * 100 // attempted) >= settings.fail_rate_alert_pct:
            problems.append({"kind": "high_failrate", "business_id": r["id"], "severity": "warn",
                             "title": f"{name}: high send-failure rate today",
                             "body": f"{r['failed_today']} of {attempted} sends failed today."})
    return problems
