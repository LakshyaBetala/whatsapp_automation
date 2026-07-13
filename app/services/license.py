"""License + heartbeat - the server-authoritative subscription answer.

The installed client (Tally agent + desktop app) is a thin pipe. It calls
``POST /license/heartbeat`` periodically and OBEYS whatever the server returns:
plan, expiry, remaining messages, debtor cap, feature flags, and update info.
The real enforcement still lives server-side (whatsapp.send_message blocks a
suspended business's sends) - the heartbeat just lets the client show the right
state and stop wasting effort. Never trust the client.

``build_heartbeat`` is the single place that assembles that answer, so the
dashboard, the agent, and any future ops view all agree on one truth.
"""
from __future__ import annotations

import datetime as _dt
import secrets
from typing import Optional

from app.config import settings
from app.models import PLAN_LABELS, PLAN_LIMITS, Plan
from app.services import subscription as subs


def generate_license_key() -> str:
    """A short, human-readable licence identity, e.g. 'ASVA-3B6A-CA12-9F0E'.
    Shown to the owner and used for support; NOT a secret (agent_token is)."""
    raw = secrets.token_hex(6).upper()  # 12 hex chars
    return "ASVA-" + "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def ensure_license_key(db, biz: dict) -> str:
    """Return the business's licence key, generating + persisting one if absent.
    Retries on the rare unique-index collision."""
    key = (biz.get("license_key") or "").strip()
    if key:
        return key
    for _ in range(5):
        candidate = generate_license_key()
        try:
            db.table("businesses").update({"license_key": candidate}).eq("id", biz["id"]).execute()
            return candidate
        except Exception:
            continue  # unique collision - try another
    return ""  # best-effort: never fail a heartbeat over a display key


def _plan_of(biz: dict) -> Plan:
    try:
        return Plan(biz.get("plan") or "starter")
    except ValueError:
        return Plan.starter


def active_debtor_count(db, business_id: str) -> int:
    """Parties with a WhatsApp number AND an open bill - the billing metric
    (what the owner pays for and what drives cost). Paged for big shops."""
    open_ids: set = set()
    start = 0
    while True:
        rows = (db.table("bills").select("client_id")
                .eq("business_id", business_id)
                .in_("status", ["pending", "partial", "overdue"])
                .range(start, start + 999).execute()).data or []
        for r in rows:
            if r.get("client_id"):
                open_ids.add(r["client_id"])
        if len(rows) < 1000:
            break
        start += 1000
    if not open_ids:
        return 0
    with_phone: set = set()
    ids = list(open_ids)
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        rows = (db.table("clients").select("id, whatsapp_number")
                .eq("business_id", business_id).in_("id", chunk).execute()).data or []
        for r in rows:
            if r.get("whatsapp_number"):
                with_phone.add(r["id"])
    return len(with_phone)


def messages_used_this_month(db, business_id: str, today: Optional[_dt.date] = None) -> int:
    today = today or _dt.date.today()
    period = today.replace(day=1).isoformat()
    r = (db.table("usage").select("message_count")
         .eq("business_id", business_id).eq("period_month", period)
         .limit(1).execute())
    return int(r.data[0]["message_count"]) if r.data else 0


def _vparts(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v or "0").strip().split("."))
    except ValueError:
        return (0,)


def _latest_release(db) -> tuple[str, bool]:
    """(latest_version, mandatory) from app_releases, best-effort."""
    try:
        r = (db.table("app_releases").select("version, mandatory")
             .order("created_at", desc=True).limit(1).execute()).data
        if r:
            return str(r[0]["version"]), bool(r[0].get("mandatory"))
    except Exception:
        pass
    return settings.app_version, False


def feature_flags(status: str) -> dict:
    """What the client is allowed to do, derived from subscription status.
    Advisory to the client; the server still blocks sends independently.

    suspended: the paid actions stop (send/reminders/digest/ocr) so the owner
    is nudged to renew - but Tally SYNC stays on (read-only, keeps the data
    fresh so the moment they renew everything is current, and the dashboard
    never lies about what is owed)."""
    live = status in ("active", "grace")
    return {
        "send": live,
        "reminders": live,
        "digest": live,
        "ocr": live,
        "sync": True,
    }


def renew_expiry(current: Optional[str | _dt.date], months: int = 1,
                 today: Optional[_dt.date] = None) -> _dt.date:
    """New expiry after paying for ``months`` cycles.

    A month = settings.subscription_cycle_days (30). Renewing an ACTIVE sub
    stacks onto its remaining time (renew from the current expiry); renewing a
    LAPSED sub starts fresh from today. So paying on time never loses days, and
    paying late never back-dates. months can be a fraction for a trial/pro-rata.
    """
    today = today or _dt.date.today()
    base = today
    if current:
        cur = current if isinstance(current, _dt.date) else _dt.date.fromisoformat(str(current)[:10])
        if cur > today:
            base = cur
    return base + _dt.timedelta(days=round(settings.subscription_cycle_days * months))


def build_heartbeat(db, biz: dict, today: Optional[_dt.date] = None) -> dict:
    """The authoritative subscription answer for one business."""
    today = today or _dt.date.today()
    plan = _plan_of(biz)
    limits = PLAN_LIMITS[plan]
    exp = biz.get("plan_expires_on")
    status = subs.effective_status(exp, today)
    dleft = subs.days_left(exp, today)

    used = messages_used_this_month(db, biz["id"], today)
    msg_limit = int(limits["messages"])
    debtors = active_debtor_count(db, biz["id"])
    debtor_cap = int(limits["debtors"])

    latest, mandatory = _latest_release(db)
    update_available = _vparts(latest) > _vparts(settings.app_version)

    return {
        "ok": True,
        "business_id": biz["id"],
        "business_name": biz.get("business_name") or "",
        "license_key": ensure_license_key(db, biz),
        "status": status,                       # active | grace | suspended
        "plan": plan.value,
        "plan_label": PLAN_LABELS.get(plan, plan.value.title()),
        "price": int(limits.get("price", 0)),
        "plan_expires_on": str(exp)[:10] if exp else None,
        "days_left": dleft,
        "messages_used": used,
        "messages_limit": msg_limit,
        "messages_remaining": max(0, msg_limit - used),
        "active_debtors": debtors,
        "debtor_cap": debtor_cap,
        "over_debtor_cap": debtors > debtor_cap,
        "features": feature_flags(status),
        "server_version": settings.app_version,
        "latest_version": latest,
        "update_available": update_available,
        "update_mandatory": mandatory and update_available,
        "server_time": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
