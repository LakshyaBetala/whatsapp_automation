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
from app.models import Lang, MessageType, Plan, PLAN_LIMITS
from app.services import upi, whatsapp
from app.services.templates import apply_discount, inr, render

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


def cadence_points(
    cadence: list[int],
    repeat_days: int,
    max_repeats: int,
    credit_days: int,
    due_offset: int,
) -> list[tuple[int, str]]:
    """All reminder points for one bill as (days_since_invoice, kind).

    The cadence SCALES with the party's credit period: [3,7,15,21,30]
    means "10%, 25%, 50%, 70%, 100% of the term". A 90-day party is
    nudged at days 9/21/45/63/90; a 7-day party at 1/2/4/5/7. After the
    due date everyone gets the overdue track (every repeat_days,
    max_repeats times) and finally one owner escalation.

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
    eff_repeat = max(repeat_days, round(repeat_days * horizon / CADENCE_BASE_DAYS))
    for k in range(1, max_repeats + 1):
        points.append((horizon + eff_repeat * k, "overdue"))
    points.append((horizon + eff_repeat * (max_repeats + 1), "escalate"))

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
    """One message per (bill, cadence-day) - the pair is the dedup key."""
    db = require_db()
    resp = (
        db.table("messages")
        .select("id", count="exact")
        .eq("bill_id", bill_id)
        .eq("reminder_day", reminder_day)
        .eq("type", "reminder")
        .limit(1)
        .execute()
    )
    return bool(resp.data)


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

    # ── Fetch all businesses with reminders enabled ───────────────────
    biz_resp = (
        db.table("businesses")
        .select(
            "id, business_name, whatsapp_number, plan, blackout_dates, "
            "reminders_enabled, upi_vpa, reminder_cadence, weekly_off_day, "
            "reminder_style, reminder_custom_line, reminder_hour, msg_language, "
            "discount_pct, overdue_repeat_days, overdue_max_repeats, plan_expires_on, "
            "reminder_batches"
        )
        .eq("reminders_enabled", True)
        .execute()
    )
    from app.services import subscription as subs
    # The sweep runs hourly. A business is processed once the current hour has
    # reached its reminder_hour - so if the laptop was off at that hour, the
    # send still happens the next hour it comes on. Per-bill dedup keeps each
    # reminder to one send/day. There is NO weekly-off: reminders can go any day.
    # Only calendar-marked holidays (blackout_dates) pause a day, and that
    # reminder shifts to the next non-holiday day (handled per-bill below).
    businesses = {
        b["id"]: b for b in (biz_resp.data or [])
        if subs.effective_status(b.get("plan_expires_on")) != "suspended"
        and now_hour >= _biz_hour(b)
    }
    if not businesses:
        log.info("Reminder sweep - nothing to do this hour (before send time or none enabled)")
        return

    # ── Fetch ALL open bills with client data in one query ────────────
    bills_resp = (
        db.table("bills")
        .select(
            "id, invoice_number, amount, outstanding, status, due_date, "
            "invoice_date, business_id, client_id, "
            "clients(id, name, whatsapp_number, language, reminders_enabled, credit_days, reminder_batch)"
        )
        .in_("status", ["pending", "partial", "overdue"])
        .in_("business_id", list(businesses.keys()))
        .execute()
    )
    open_bills = bills_resp.data or []
    log.info("Reminder sweep - %d open bills across %d businesses", len(open_bills), len(businesses))

    sent = 0
    skipped = 0
    # Daily cap per business: a fresh backlog (e.g. 200 overdue bills on
    # day one) drips out over days instead of blasting in one sweep.
    sent_per_biz: dict[str, int] = {}
    cap = settings.daily_reminder_cap

    for bill in open_bills:
        biz = businesses.get(bill["business_id"])
        if not biz:
            continue
        if cap > 0 and sent_per_biz.get(bill["business_id"], 0) >= cap:
            skipped += 1
            continue
        client = bill.get("clients") or {}

        # ── SKIP 1: bill status (fastest, no join needed) ─────────────
        if bill["status"] == "paid":
            skipped += 1
            continue

        # ── SKIP 2: client reminders toggle ───────────────────────────
        if not client.get("reminders_enabled", True):
            skipped += 1
            continue

        # ── SKIP 3: work out where this bill sits in the cadence ─────
        try:
            invoice_date = date.fromisoformat(str(bill["invoice_date"]))
        except (TypeError, ValueError):
            skipped += 1
            continue
        due_str = bill.get("due_date")
        due_date = date.fromisoformat(str(due_str)) if due_str else invoice_date
        days_since_invoice = (today - invoice_date).days
        due_offset = (due_date - invoice_date).days

        # Per-party reminder batch: its severity (style) drives the cadence and
        # tone, its language/discount/custom-line drive the copy. Falls back to
        # the business's global settings when no batches are configured.
        from app.services.batches import resolve_batch
        batch = resolve_batch(biz, client.get("reminder_batch"))
        batch_style = batch["style"]
        batch_lang = batch["lang"]

        points = cadence_points(
            cadence=STYLE_CADENCE.get(batch_style, biz.get("reminder_cadence") or DEFAULT_CADENCE),
            repeat_days=biz.get("overdue_repeat_days") or 7,
            max_repeats=biz.get("overdue_max_repeats") or 3,
            credit_days=client.get("credit_days") or 30,
            due_offset=due_offset,
        )
        applicable = latest_reached_point(points, days_since_invoice)
        if applicable is None:
            continue
        applicable_day, kind = applicable

        # ── SKIP 4: dedup - already sent this (bill, day) pair? ───────
        if await _already_sent(bill["id"], applicable_day):
            continue

        # ── SKIP 5: blackout dates ────────────────────────────────────
        blackout_strs = [str(d) for d in (biz.get("blackout_dates") or [])]
        if today.isoformat() in blackout_strs:
            skipped += 1
            continue

        # ── SKIP 6: plan limit (most expensive - last check) ─────────
        plan = Plan(biz["plan"])
        plan_limit = PLAN_LIMITS[plan]["messages"]
        limit_resp = db.rpc("increment_usage_if_allowed", {
            "p_business_id": bill["business_id"],
            "p_limit": plan_limit,
        }).execute()
        limit_data = limit_resp.data
        if isinstance(limit_data, list):
            limit_data = limit_data[0] if limit_data else {}
        if not limit_data.get("allowed", False):
            log.warning("Plan limit reached for %s during reminder sweep", bill["business_id"])
            continue

        # ══════════════════════════════════════════════════════════════
        # SEND
        # ══════════════════════════════════════════════════════════════
        client_lang = Lang(client.get("language", "hi"))
        outstanding = Decimal(str(bill["outstanding"]))
        outstanding_fmt = inr(outstanding)
        invoice_num = bill.get("invoice_number") or "-"
        client_name = client.get("name", "Customer")
        days_since_due = max((today - due_date).days, 0)
        biz_name = biz.get("business_name", "")

        # Early-payment discount (from the party's batch): the QR + shown amount
        # drop by the batch discount, and a discount line is appended - only when
        # the batch actually sets a discount (else no line at all).
        pay_amount, discount_line = apply_discount(
            outstanding, batch["disc"], batch_lang)

        # UPI link + QR (attached to customer messages when VPA is set)
        vpa = biz.get("upi_vpa")
        pay_link = upi.upi_link(vpa, biz_name, pay_amount, invoice_num) if vpa else ""
        qr_b64 = upi.qr_png_base64(pay_link) if (vpa and kind != "escalate") else None

        if kind == "escalate":
            # ── ESCALATION → goes to OWNER, not customer ──────────────
            alert_text = (
                f"{client_name} ka {outstanding_fmt} abhi bhi baaki hai "
                f"({days_since_due} din overdue, sab reminders bhej diye). "
                f"Bill: {invoice_num}. Ab seedha baat karein."
            )
            await whatsapp.send_template(
                business_id=bill["business_id"],
                to_number=biz["whatsapp_number"],  # OWNER number
                campaign_name="owner_alert_hi",
                template_params=[biz_name, alert_text],
                business_name=biz_name,
                plan=plan,
                message_type=MessageType.reminder,
                reminder_day=applicable_day,
                client_id=bill["client_id"],
                bill_id=bill["id"],
                language=Lang.hi,
                message_text=f"{biz_name}: {alert_text}",
                channel="platform",
            )
            log.info("Escalation → OWNER for bill %s (%s)", invoice_num, client_name)
        else:
            # ── NUDGE / PRE-DUE / OVERDUE → goes to customer ──────────
            client_phone = client.get("whatsapp_number")
            if not client_phone:
                log.info("Skipping reminder for %s - no WhatsApp number", client_name)
                continue

            template_key = "overdue" if kind == "overdue" else "reminder"
            style = batch_style
            # English preference swaps to the _en templates (no tone split there).
            if batch_lang == "english":
                template_key += "_en"
                style = "standard"
            tpl_name, body = render(
                template_key,
                client_lang,
                style=style,
                client=client_name,
                business=biz_name,
                invoice_number=invoice_num,
                outstanding=outstanding_fmt,
                days_overdue=str(days_since_due),
                upi_link=pay_link or "owner se UPI details maangein",
            )
            # Early-payment discount line (auto), then owner's optional custom line.
            if discount_line:
                body = f"{body}\n\n{discount_line}"
            custom_line = (batch.get("line") or "").strip()
            if custom_line:
                body = f"{body}\n\n{custom_line}"

            await whatsapp.send_template(
                business_id=bill["business_id"],
                to_number=client_phone,
                campaign_name=tpl_name,
                template_params=[
                    client_name, biz_name, invoice_num,
                    outstanding_fmt, str(days_since_due),
                ],
                business_name=biz_name,
                plan=plan,
                message_type=MessageType.reminder,
                reminder_day=applicable_day,
                client_id=bill["client_id"],
                bill_id=bill["id"],
                language=client_lang,
                message_text=body,
                image_base64=qr_b64,
                image_filename=f"pay_{invoice_num}.png",
            )

        sent += 1
        sent_per_biz[bill["business_id"]] = sent_per_biz.get(bill["business_id"], 0) + 1

        # ── Pace sends so we never blast many messages at once ────────────
        # A burst of identical-looking sends on an unofficial WhatsApp link is
        # the #1 ban trigger. Sleep a randomised gap between each send so the
        # traffic looks human. The daily cap above bounds total volume.
        await asyncio.sleep(random.uniform(settings.send_gap_min_s, settings.send_gap_max_s))

    log.info("Reminder sweep complete - sent=%d, skipped=%d", sent, skipped)
