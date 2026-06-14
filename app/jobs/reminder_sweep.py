"""Reminder sweep — checks open bills daily and sends reminders.

Runs at 10 AM IST daily.  Fetches ALL open bills in one query, then applies
skip checks in this exact, non-negotiable order (fastest first):

  1. Bill status == paid         → skip (no join)
  2. Client reminders toggle     → skip (one column)
  3. Due date not reached        → skip (Instruction 3 — CRITICAL)
  4. No applicable reminder day  → skip (arithmetic)
  5. Already sent this (bill, day) pair → skip (dedup query)
  6. Business blackout date      → skip (array lookup)
  7. Plan limit exceeded         → skip (Postgres RPC)

Day 45 is special: message goes to the OWNER (escalation), not the customer.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.db import require_db
from app.models import Lang, MessageType, Plan, PLAN_LIMITS, REMINDER_DAYS
from app.services import whatsapp
from app.services.templates import inr, render

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> date:
    return datetime.now(IST).date()


async def _already_sent(bill_id: str, reminder_day: int) -> bool:
    """Check if this (bill_id, reminder_day) pair was already sent.

    Dedup key is the PAIR, not just bill_id — a bill legitimately gets
    Day 7, Day 15, etc.  Only one message per (bill, day).
    """
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


async def run() -> None:
    """Scheduled at 10 AM IST daily. Sweep all open bills and send reminders."""
    db = require_db()
    today = _today_ist()

    # ── Fetch all businesses with reminders enabled ───────────────────
    biz_resp = (
        db.table("businesses")
        .select(
            "id, business_name, whatsapp_number, plan, "
            "blackout_dates, reminders_enabled"
        )
        .eq("reminders_enabled", True)
        .execute()
    )
    businesses = {b["id"]: b for b in (biz_resp.data or [])}
    if not businesses:
        log.info("Reminder sweep — no businesses with reminders enabled")
        return

    # ── Fetch ALL open bills with client data in one query ────────────
    bills_resp = (
        db.table("bills")
        .select(
            "id, invoice_number, amount, outstanding, status, due_date, "
            "invoice_date, business_id, client_id, "
            "clients(id, name, whatsapp_number, language, reminders_enabled)"
        )
        .in_("status", ["pending", "partial", "overdue"])
        .in_("business_id", list(businesses.keys()))
        .execute()
    )
    open_bills = bills_resp.data or []
    log.info("Reminder sweep — %d open bills across %d businesses", len(open_bills), len(businesses))

    sent = 0
    skipped = 0

    for bill in open_bills:
        biz = businesses.get(bill["business_id"])
        if not biz:
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

        # ── SKIP 3: due date not reached (CRITICAL — Instruction 3) ──
        #    Reminders count from due_date, NOT invoice_date.
        #    A farmer with 180-day credit must NOT get Day 7 reminder
        #    at day 7 from invoice — only after the credit period expires.
        due_date_str = bill.get("due_date")
        if not due_date_str:
            skipped += 1
            continue
        due_date = date.fromisoformat(str(due_date_str))
        days_since_due = (today - due_date).days
        if days_since_due < 0:
            # Not due yet — never send early
            continue

        # ── SKIP 4: find applicable reminder day ──────────────────────
        #    Walk REMINDER_DAYS ascending; keep the highest day <= days_since_due.
        #    e.g. 32 days overdue → applicable_day = 30
        applicable_day = None
        for day in REMINDER_DAYS:
            if days_since_due >= day:
                applicable_day = day
        if applicable_day is None:
            # Less than 7 days overdue — no reminder yet
            continue

        # ── SKIP 5: dedup — already sent this (bill, day) pair? ───────
        if await _already_sent(bill["id"], applicable_day):
            continue

        # ── SKIP 6: blackout dates ────────────────────────────────────
        blackout = biz.get("blackout_dates") or []
        # blackout_dates can be date[] from Postgres — compare as strings
        blackout_strs = [str(d) for d in blackout]
        if today.isoformat() in blackout_strs:
            skipped += 1
            continue

        # ── SKIP 7: plan limit (most expensive — last check) ─────────
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
            log.warning(
                "Plan limit reached for %s during reminder sweep",
                bill["business_id"],
            )
            continue

        # ══════════════════════════════════════════════════════════════
        # SEND REMINDER
        # ══════════════════════════════════════════════════════════════

        client_lang = Lang(client.get("language", "hi"))
        outstanding_fmt = inr(Decimal(str(bill["outstanding"])))
        invoice_num = bill.get("invoice_number") or "—"
        client_name = client.get("name", "Customer")

        if applicable_day == 45:
            # ── DAY 45: ESCALATION → goes to OWNER, not customer ──────
            alert_text = (
                f"{client_name} ka {outstanding_fmt} abhi bhi baaki hai "
                f"({days_since_due} din overdue). Bill: {invoice_num}. "
                f"Seedha baat karein."
            )
            await whatsapp.send_template(
                business_id=bill["business_id"],
                to_number=biz["whatsapp_number"],  # OWNER number
                campaign_name="owner_alert_hi",
                template_params=[
                    biz.get("business_name", ""),
                    alert_text,
                ],
                business_name=biz.get("business_name", ""),
                plan=plan,
                message_type=MessageType.reminder,
                reminder_day=applicable_day,
                client_id=bill["client_id"],
                bill_id=bill["id"],
                language=Lang.hi,
            )
            log.info(
                "Day 45 escalation → OWNER for bill %s (%s)",
                invoice_num,
                client_name,
            )
        else:
            # ── STANDARD REMINDER → goes to customer ──────────────────
            client_phone = client.get("whatsapp_number")
            if not client_phone:
                log.info(
                    "Skipping reminder for %s — no WhatsApp number",
                    client_name,
                )
                continue

            tpl_name, _ = render(
                "reminder",
                client_lang,
                client=client_name,
                business=biz.get("business_name", ""),
                invoice_number=invoice_num,
                outstanding=outstanding_fmt,
                days_overdue=str(days_since_due),
                upi_link="",  # populated in PR 4 when bill has upi_link
            )

            await whatsapp.send_template(
                business_id=bill["business_id"],
                to_number=client_phone,
                campaign_name=tpl_name,
                template_params=[
                    client_name,
                    biz.get("business_name", ""),
                    invoice_num,
                    outstanding_fmt,
                    str(days_since_due),
                ],
                business_name=biz.get("business_name", ""),
                plan=plan,
                message_type=MessageType.reminder,
                reminder_day=applicable_day,
                client_id=bill["client_id"],
                bill_id=bill["id"],
                language=client_lang,
            )

        sent += 1

    log.info("Reminder sweep complete — sent=%d, skipped=%d", sent, skipped)
