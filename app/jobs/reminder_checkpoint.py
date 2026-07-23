"""Morning pre-reminder checkpoint job.

Runs hourly (like the sweep). Each active business fires ONCE, at
(its reminder_hour - checkpoint_lead_hours), before the sweep sends. It computes
today's reminder candidates, stores them (app/services/checkpoint.py), and messages
the owner on the bot number so they can HOLD anyone who already paid. Owner-facing
only - customers are never touched here.

Option A: a HOLD skips today's reminder and nudges the owner to enter the receipt
in Tally. Nothing is marked paid and nothing is written to Tally.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import checkpoint, whatsapp
from app.services import subscription as subs
from app.services.templates import inr
from app.jobs.reminder_sweep import (DEFAULT_CADENCE, IST, _already_sent,
                                     _biz_hour, cadence_points, latest_reached_point)

log = logging.getLogger(__name__)

MAX_LIST = 15   # keep the message readable; parties past this still get reminded


async def _due_parties(db, biz: dict, today: date) -> list[dict]:
    """Parties with a reminder due today for this business, ranked by amount x
    days-overdue. Returns [{id, name, amount, days}]. Mirrors the sweep's
    selection (a cadence point is reached and not already sent), minus the
    send-hour/cap gating - so the preview is 'who is due today'."""
    bills = (db.table("bills")
             .select("id, outstanding, status, due_date, invoice_date, client_id, "
                     "clients(id, name, whatsapp_number, reminders_enabled, credit_days, "
                     "reminder_anchor, created_at)")
             .in_("status", ["pending", "partial", "overdue"])
             .eq("business_id", biz["id"]).execute()).data or []

    parties: dict = {}
    for b in bills:
        parties.setdefault(b["client_id"],
                           {"client": b.get("clients") or {}, "bills": []})["bills"].append(b)

    out: list[dict] = []
    for cid, p in parties.items():
        c = p["client"]
        if not c.get("reminders_enabled", True) or not c.get("whatsapp_number"):
            continue
        anchor = None
        araw = c.get("reminder_anchor") or c.get("created_at")
        if araw:
            try:
                anchor = date.fromisoformat(str(araw)[:10])
            except (TypeError, ValueError):
                anchor = None

        total = Decimal(0)
        oldest = 0
        due_today = False
        for bill in p["bills"]:
            try:
                inv = date.fromisoformat(str(bill["invoice_date"]))
            except (TypeError, ValueError):
                continue
            due = date.fromisoformat(str(bill["due_date"])) if bill.get("due_date") else inv
            total += Decimal(str(bill["outstanding"]))
            oldest = max(oldest, (today - inv).days)
            overdue_from = (anchor - inv).days if (anchor and anchor > due) else None
            points = cadence_points(
                cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
                repeat_days=biz.get("overdue_repeat_days") or 7,
                max_repeats=biz.get("overdue_max_repeats") or 3,
                credit_days=c.get("credit_days") or 30,
                due_offset=(due - inv).days,
                overdue_from=overdue_from,
            )
            ap = latest_reached_point(points, (today - inv).days)
            if ap and not await _already_sent(bill["id"], ap[0]):
                due_today = True
        if due_today and total > 0:
            out.append({"id": cid, "name": c.get("name") or "Customer",
                        "amount": float(total), "days": oldest})

    out.sort(key=lambda x: x["amount"] * (x["days"] + 1), reverse=True)
    return out


def _message(items: list[dict]) -> str:
    shown = items[:MAX_LIST]
    total = sum(i["amount"] for i in items)
    lines = [f"Good morning. Today I plan to remind these {len(items)} parties ({inr(total)}):", ""]
    for i, it in enumerate(shown, 1):
        lines.append(f"{i}. {it['name']}  {inr(it['amount'])}  {it['days']} days")
    if len(items) > MAX_LIST:
        lines.append(f"...and {len(items) - MAX_LIST} more.")
    lines += [
        "",
        "Already been paid by any? Reply:  PAID 1   (or  PAID <name>)",
        "I will hold that reminder and remind you to enter it in Tally.",
        "Reply OK to send all, or HOLD to pause today.",
    ]
    return "\n".join(lines)


async def run() -> None:
    """Hourly. Each business previews once, at reminder_hour - checkpoint_lead_hours."""
    if not settings.enable_reminder_checkpoint:
        return
    db = require_db()
    now = datetime.now(IST)
    today = now.date()

    biz_rows = (db.table("businesses")
                .select("id, business_name, whatsapp_number, plan, reminders_enabled, "
                        "reminder_cadence, reminder_hour, overdue_repeat_days, "
                        "overdue_max_repeats, plan_expires_on, checkpoint_date")
                .eq("reminders_enabled", True).execute()).data or []

    for biz in biz_rows:
        try:
            if subs.effective_status(biz.get("plan_expires_on")) == "suspended":
                continue
            cp_hour = max(0, _biz_hour(biz) - max(1, settings.checkpoint_lead_hours))
            if now.hour != cp_hour:
                continue
            if str(biz.get("checkpoint_date") or "")[:10] == today.isoformat():
                continue   # already previewed today

            items = await _due_parties(db, biz, today)
            checkpoint.set_today(db, biz["id"], items)   # marks 'previewed' even if empty
            if not items or not biz.get("whatsapp_number"):
                continue

            await whatsapp.send_template(
                business_id=biz["id"],
                to_number=biz["whatsapp_number"],
                campaign_name="checkpoint",
                template_params=[biz.get("business_name", ""), ""],
                business_name=biz.get("business_name", ""),
                plan=Plan(biz["plan"]),
                message_type=MessageType.owner_alert,
                language=Lang.hi,
                message_text=_message(items),
                channel="platform",     # owner-facing -> bot number, never queued
            )
            log.info("Checkpoint -> owner of %s (%d parties)", biz["id"], len(items))
        except Exception:
            log.exception("Checkpoint failed for business %s - continuing", biz.get("id"))
