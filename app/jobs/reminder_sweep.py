"""Reminder sweep - the collection cadence engine. Runs at 10 AM IST daily.

Cadence (per business, configurable via businesses.reminder_cadence):

  Regular trade (client credit_days <= 30):
      invoice day +3, +7, +15, +21, +30 - gentle "please pay" nudges
      (points past the due date automatically use the overdue tone)
  Credit-terms clients (credit_days > 30, e.g. 45/60/90-day companies):
      one courtesy heads-up 3 days BEFORE due - no early nagging
  Everyone, once past due:
      overdue message every `overdue_repeat_days` (default 7),
      `overdue_max_repeats` times (default 3)
  Finally:
      one escalation to the OWNER - "call them yourself"

Every message carries the UPI link + QR image when the business has a
upi_vpa. Dedup is per (bill, cadence-day) via the messages table, so a
bill never gets the same reminder twice. Skip checks run cheapest-first.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import whatsapp
from app.services.templates import inr

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_CADENCE = [3, 7, 15, 21, 30]

# Reminder "style" → base cadence (authored for a 30-day term; cadence_points
# scales it to each party's real credit period). The dashboard writes the
# chosen style's cadence into businesses.reminder_cadence, and the style name
# into businesses.reminder_style (which picks the message tone).
STYLE_CADENCE = {
    "gentle": [7, 15, 30],
    "standard": [3, 7, 15, 21, 30],
    # Capped at 6 touches (was 7): protects margin + avoids over-messaging a
    # customer, while still hitting earlier and more often than standard.
    "firm": [2, 7, 15, 22, 30, 45],
}

_KIND_RANK = {"nudge": 0, "predue": 1, "overdue": 2, "escalate": 3}


def _today_ist() -> date:
    return datetime.now(IST).date()


def _biz_hour(b: dict) -> int:
    """The hour (0-23) this business wants reminders sent. Default 11."""
    h = b.get("reminder_hour")
    return 11 if h is None else int(h)


CADENCE_BASE_DAYS = 30  # the cadence numbers are authored for a 30-day term

# The overdue track keeps repeating until the party PAYS or this many days
# after the track starts (due date, or the day the owner selected the party) -
# whichever comes first. Then one final "call them yourself" owner escalation.
OVERDUE_WINDOW_DAYS = 200


def cadence_points(
    cadence: list[int],
    repeat_days: int,
    max_repeats: int,
    credit_days: int,
    due_offset: int,
    overdue_from: int | None = None,
) -> list[tuple[int, str]]:
    """All reminder points for one bill as (days_since_invoice, kind).

    The cadence SCALES with the party's credit period: [3,7,15,21,30]
    means "10%, 25%, 50%, 70%, 100% of the term". A 90-day party is
    nudged at days 9/21/45/63/90; a 7-day party at 1/2/4/5/7. After the
    due date everyone gets the overdue track (every repeat_days,
    max_repeats times) and finally one owner escalation.

    overdue_from: days-since-invoice at which the overdue track STARTS
    (defaults to the due date). Passed as (max(due, anchor) - invoice) when
    the owner selected the party after the bill was already overdue - the
    day you select a party, ASVA starts counting from that day: the party
    gets the OVERDUE message that same day (it IS overdue - no pretending),
    then every repeat interval, until paid or the window runs out.

    The overdue track repeats until PAID or OVERDUE_WINDOW_DAYS (200) after
    the track starts, whichever comes first - then one owner escalation.
    (max_repeats is kept for signature compatibility; the window rules.)

    kind: nudge | overdue | escalate. Pure function - unit tested.
    due_offset = due_date - invoice_date in days (the credit period).
    """
    points: list[tuple[int, str]] = []
    horizon = max(due_offset, 1)
    for d in cadence:
        day = round(d * horizon / CADENCE_BASE_DAYS)
        if day >= 1:
            points.append((min(day, horizon), "nudge"))

    # Overdue spacing scales with the term: a 30-day party keeps ~repeat_days,
    # a 90-day party gets ~3x that, so long-credit trades aren't nagged weekly.
    base = max(horizon, overdue_from or 0)
    eff_repeat = max(repeat_days, round(repeat_days * horizon / CADENCE_BASE_DAYS))
    if base > horizon:
        # Selected while already overdue: say so THAT day, factually.
        points.append((base, "overdue"))
    n_repeats = max(1, OVERDUE_WINDOW_DAYS // eff_repeat)
    for k in range(1, n_repeats + 1):
        points.append((base + eff_repeat * k, "overdue"))
    points.append((base + eff_repeat * (n_repeats + 1), "escalate"))

    # Collapse same-day collisions, strongest kind wins
    best: dict[int, str] = {}
    for day, kind in points:
        if day not in best or _KIND_RANK[kind] > _KIND_RANK[best[day]]:
            best[day] = kind
    return sorted(best.items())


def latest_reached_point(
    points: list[tuple[int, str]], days_since_invoice: int
) -> tuple[int, str] | None:
    """The single most-recent cadence point due by today (or None).

    This is what makes "next working day" and "the laptop was off" resolve to
    exactly ONE message: if earlier points were skipped (off-day, holiday, or
    the host was offline) and never marked sent, the sweep still returns only
    the latest reached point - so catching up never fires a backlog of stacked
    reminders. Pure function; ``points`` must be sorted (cadence_points is).
    """
    applicable = None
    for day, kind in points:
        if days_since_invoice >= day:
            applicable = (day, kind)
    return applicable


async def _already_sent(bill_id: str, reminder_day: int) -> bool:
    """One message per (bill, cadence-day) - the pair is the dedup key.
    FAILED rows don't count: if WhatsApp was down when the sweep fired, the
    reminder retries on the next hourly sweep instead of being lost forever
    (sent and queued rows both block a resend)."""
    db = require_db()
    resp = (
        db.table("messages")
        .select("id", count="exact")
        .eq("bill_id", bill_id)
        .eq("reminder_day", reminder_day)
        .eq("type", "reminder")
        .neq("delivery_status", "failed")
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def _stamp_heartbeat(db, today: date, now_hour: int) -> set[int]:
    """Record 'the sweep ran at this hour today' and return ALL hours that ran
    today. A batch hour with NO stamp means ASVA was off at that hour - the
    late send then waits for the owner's confirmation instead of firing
    silently. Best-effort: if migration 022 is missing, behave like before
    (every hour 'ran' -> no confirmation gate)."""
    try:
        db.table("sweep_runs").upsert(
            {"run_date": today.isoformat(), "run_hour": now_hour},
            on_conflict="run_date,run_hour",
        ).execute()
        rows = (db.table("sweep_runs").select("run_hour")
                .eq("run_date", today.isoformat()).execute()).data or []
        # This run counts too (true by construction, even if the read raced).
        return {int(r["run_hour"]) for r in rows} | {now_hour}
    except Exception:
        log.warning("sweep_runs unavailable (apply migration 022) - catch-up confirmation off")
        return set(range(24))


def _catchup_decision(biz: dict, today: date) -> str | None:
    """The owner's decision for TODAY's missed-hour catch-up: 'send', 'skip',
    or None (undecided - hold the sends and alert the owner)."""
    if str(biz.get("catchup_date") or "")[:10] != today.isoformat():
        return None
    action = (biz.get("catchup_action") or "").strip().lower()
    return action if action in ("send", "skip") else None


async def _alert_catchup_pending(db, biz: dict, n_parties: int, total: Decimal,
                                 missed_hours: list[int], today: date) -> None:
    """Tell the owner ONCE per day: ASVA was off at the send hour; reminders
    are waiting for their go-ahead (dashboard banner has Send / Skip)."""
    try:
        dup = (db.table("messages").select("id", count="exact")
               .eq("business_id", biz["id"])
               .eq("template_name", "catchup_notice")
               .gte("created_at", today.isoformat())
               .limit(1).execute())
        if dup.data:
            return
    except Exception:
        pass
    hrs = ", ".join(f"{h:02d}:00" for h in sorted(set(missed_hours)))
    text = (f"ASVA was not running at {hrs} today.\n\n"
            f"{n_parties} parties' reminders are waiting ({inr(total)}).\n\n"
            f"Open ASVA and press Send now, or Skip today.\n"
            f"Nothing will be sent without you.")
    try:
        await whatsapp.send_template(
            business_id=biz["id"],
            to_number=biz["whatsapp_number"],
            campaign_name="catchup_notice",
            template_params=[biz.get("business_name", ""), text],
            business_name=biz.get("business_name", ""),
            plan=Plan(biz["plan"]),
            message_type=MessageType.reminder,
            language=Lang.hi,
            message_text=f"{biz.get('business_name', '')}: {text}",
            channel="platform",
        )
        log.info("Catch-up pending alert -> owner (%d parties, %s)", n_parties, inr(total))
    except Exception:
        log.exception("Catch-up owner alert failed")


def _mark_overdue(db, today: date) -> None:
    """Flip past-due 'pending' bills to 'overdue' so LIST/dashboards report
    correctly. 'partial' stays partial - it carries payment information."""
    try:
        resp = (
            db.table("bills")
            .update({"status": "overdue"})
            .eq("status", "pending")
            .lt("due_date", today.isoformat())
            .execute()
        )
        flipped = len(resp.data or [])
        if flipped:
            log.info("Marked %d bills overdue", flipped)
    except Exception:
        log.exception("Overdue flip failed - sweep continues")


async def run() -> None:
    """Scheduled at 10 AM IST daily. Sweep all open bills and send reminders."""
    db = require_db()
    now = datetime.now(IST)
    today = now.date()
    weekday = today.weekday()  # Mon=0 .. Sun=6
    now_hour = now.hour

    # ── Flip past-due pending bills to overdue first ──────────────────
    # (status accuracy still matters on a weekly-off day, so this runs
    # regardless of whether reminders go out today.)
    _mark_overdue(db, today)

    # ── Heartbeat: which hours did the sweep actually run today? ──────
    hours_run = _stamp_heartbeat(db, today, now_hour)

    # ── Fetch all businesses with reminders enabled ───────────────────
    biz_resp = (
        db.table("businesses")
        .select(
            "id, business_name, whatsapp_number, plan, blackout_dates, "
            "reminders_enabled, upi_vpa, reminder_cadence, weekly_off_day, "
            "reminder_style, reminder_custom_line, reminder_hour, msg_language, "
            "discount_pct, overdue_repeat_days, overdue_max_repeats, plan_expires_on, "
            "reminder_batches, catchup_date, catchup_action"
        )
        .eq("reminders_enabled", True)
        .execute()
    )
    from app.services import subscription as subs
    # The sweep runs hourly. SEND TIME is now per-batch (each batch has its own
    # hour), so we no longer gate the whole business here - we process every
    # active business and gate each PARTY on its batch's hour below. Catch-up
    # still works: a party sends once the current hour has reached its batch
    # hour, so a laptop that was off at that hour sends the next hour it is on.
    # Per-bill dedup keeps each reminder to one send/day. Only calendar-marked
    # holidays (blackout_dates) pause a day (handled per-bill below).
    businesses = {
        b["id"]: b for b in (biz_resp.data or [])
        if subs.effective_status(b.get("plan_expires_on")) != "suspended"
    }
    if not businesses:
        log.info("Reminder sweep - nothing to do (no active businesses)")
        return

    # ── Fetch ALL open bills with client data in one query ────────────
    bills_resp = (
        db.table("bills")
        .select(
            "id, invoice_number, amount, outstanding, status, due_date, "
            "invoice_date, business_id, client_id, "
            "clients(id, name, whatsapp_number, language, reminders_enabled, credit_days, "
            "reminder_batch, reminder_anchor, created_at)"
        )
        .in_("status", ["pending", "partial", "overdue"])
        .in_("business_id", list(businesses.keys()))
        .execute()
    )
    open_bills = bills_resp.data or []
    log.info("Reminder sweep - %d open bills across %d businesses", len(open_bills), len(businesses))

    # ── Group bills per PARTY: one party = ONE consolidated message ───
    # (Before this rework a party with 3 open bills received 3 separate
    # messages. Now the sweep sends exactly what the Send Now button sends:
    # all bills itemised, one total, one QR - in the party's batch language.)
    parties: dict[tuple, dict] = {}
    for bill in open_bills:
        if bill["status"] == "paid":
            continue
        biz = businesses.get(bill["business_id"])
        if not biz:
            continue
        key = (bill["business_id"], bill["client_id"])
        p = parties.setdefault(key, {"biz": biz, "client": bill.get("clients") or {}, "bills": []})
        p["bills"].append(bill)

    sent = 0
    skipped = 0
    # Missed-hour catch-up: parties held back waiting for the owner's
    # decision, aggregated per business for ONE owner alert per day.
    catchup_pending: dict[str, dict] = {}
    # Daily cap per business: a fresh backlog drips out over days instead of
    # blasting in one sweep. One party = one message = 1 toward the cap.
    sent_per_biz: dict[str, int] = {}
    cap = settings.daily_reminder_cap

    from app.services import bot as bot_svc
    from app.services.batches import resolve_batch, batch_hour

    for (biz_id, client_id), p in parties.items():
        try:
            biz, client = p["biz"], p["client"]
            if cap > 0 and sent_per_biz.get(biz_id, 0) >= cap:
                skipped += 1
                continue
            if not client.get("reminders_enabled", True):
                skipped += 1
                continue
            # Holiday (calendar-marked) pauses the day; catch-up next working day.
            if today.isoformat() in [str(d) for d in (biz.get("blackout_dates") or [])]:
                skipped += 1
                continue
            # Per-batch SEND TIME: wait until this party's batch hour.
            batch = resolve_batch(biz, client.get("reminder_batch"))
            bh = batch_hour(biz, batch)
            if now_hour < bh:
                continue

            # Anchor: "the day you select a party, counting starts that day".
            anchor = None
            anchor_raw = client.get("reminder_anchor") or client.get("created_at")
            if anchor_raw:
                try:
                    anchor = date.fromisoformat(str(anchor_raw)[:10])
                except (TypeError, ValueError):
                    anchor = None

            # Which bills have a cadence point due today that was never sent?
            triggers: list[tuple[dict, int, str]] = []   # (bill, day, kind)
            entry_bills: list[dict] = []
            total = Decimal(0)
            oldest_days = 0
            for bill in sorted(p["bills"], key=lambda b: str(b.get("invoice_date"))):
                try:
                    invoice_date = date.fromisoformat(str(bill["invoice_date"]))
                except (TypeError, ValueError):
                    continue
                due_str = bill.get("due_date")
                due_date = date.fromisoformat(str(due_str)) if due_str else invoice_date
                entry_bills.append(bill)
                total += Decimal(str(bill["outstanding"]))
                oldest_days = max(oldest_days, (today - invoice_date).days)

                overdue_from = None
                if anchor and anchor > due_date:
                    overdue_from = (anchor - invoice_date).days
                points = cadence_points(
                    cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
                    repeat_days=biz.get("overdue_repeat_days") or 7,
                    max_repeats=biz.get("overdue_max_repeats") or 3,
                    credit_days=client.get("credit_days") or 30,
                    due_offset=(due_date - invoice_date).days,
                    overdue_from=overdue_from,
                )
                applicable = latest_reached_point(points, (today - invoice_date).days)
                if applicable is None:
                    continue
                day, kind = applicable
                if await _already_sent(bill["id"], day):
                    continue
                triggers.append((bill, day, kind))

            if not triggers or not entry_bills:
                continue

            # Missed-hour catch-up: this party's batch hour passed with NO
            # sweep run (ASVA/laptop was off). Late sends need the owner's
            # go-ahead - 'send' releases them, 'skip' drops today, undecided
            # = hold + one owner alert. On-time sends (now_hour == bh) and
            # parties added after an on-time run are never gated.
            if now_hour > bh and bh not in hours_run:
                decision = _catchup_decision(biz, today)
                if decision != "send":
                    skipped += 1
                    if decision is None:
                        pend = catchup_pending.setdefault(
                            biz_id, {"n": 0, "total": Decimal(0), "hours": set()})
                        pend["n"] += 1
                        pend["total"] += total
                        pend["hours"].add(bh)
                    continue

            client_name = client.get("name", "Customer")
            biz_name = biz.get("business_name", "")
            escalate = any(k == "escalate" for _, _, k in triggers)

            if escalate:
                # ── Window exhausted → ONE owner escalation for the party ──
                alert_text = (
                    f"{client_name} still owes {inr(total)} "
                    f"({len(entry_bills)} bills, oldest {oldest_days} days). "
                    f"All reminders are done. Please call them directly now."
                )
                result = await whatsapp.send_template(
                    business_id=biz_id,
                    to_number=biz["whatsapp_number"],  # OWNER number
                    campaign_name="owner_alert_hi",
                    template_params=[biz_name, alert_text],
                    business_name=biz_name,
                    plan=Plan(biz["plan"]),
                    message_type=MessageType.reminder,
                    client_id=client_id,
                    bill_id=triggers[0][0]["id"],
                    language=Lang.hi,
                    message_text=f"{biz_name}: {alert_text}",
                    channel="platform",
                )
                ok = bool(result.get("sent") or result.get("queued"))
                if ok:
                    log.info("Escalation -> OWNER for %s (%s)", client_name, inr(total))
            else:
                # ── ONE consolidated customer message (same as Send Now) ──
                if not client.get("whatsapp_number"):
                    log.info("Skipping %s - no WhatsApp number", client_name)
                    continue
                entry = {"client": client, "bills": entry_bills,
                         "total": total, "oldest_days": oldest_days}
                ok, line = await bot_svc._send_consolidated_reminder(biz, entry)
                if not ok:
                    log.warning("Sweep send failed for %s: %s", client_name, line)

            if ok:
                # Mark every triggering (bill, cadence-day) so no point ever
                # fires twice; a failed send marks nothing and retries next hour.
                for bill, day, kind in triggers:
                    try:
                        db.table("messages").insert({
                            "business_id": biz_id,
                            "client_id": client_id,
                            "bill_id": bill["id"],
                            "type": MessageType.reminder.value,
                            "reminder_day": day,
                            "template_name": "cadence_marker",
                            "language": "hi",
                            "delivery_status": "sent",
                            "cost": 0,
                        }).execute()
                    except Exception:
                        log.exception("Marker insert failed for bill %s", bill["id"])
                sent += 1
                sent_per_biz[biz_id] = sent_per_biz.get(biz_id, 0) + 1
                # Human-like gap between sends (bursts are the #1 ban trigger).
                await asyncio.sleep(random.uniform(settings.send_gap_min_s, settings.send_gap_max_s))
        except Exception:
            # One bad party must NEVER kill the whole sweep.
            log.exception("Sweep failed for party %s - continuing", client_id)
            continue

    # ── One owner alert per business with held catch-up sends ─────────
    for biz_id, pend in catchup_pending.items():
        biz = businesses.get(biz_id)
        if biz and biz.get("whatsapp_number") and pend["n"]:
            await _alert_catchup_pending(
                db, biz, pend["n"], pend["total"], sorted(pend["hours"]), today)

    log.info("Reminder sweep complete - parties messaged=%d, skipped=%d, held=%d",
             sent, skipped, sum(p["n"] for p in catchup_pending.values()))
