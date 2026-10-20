"""Daily subscription check - transitions statuses and warns owners.

Runs each morning. The stored subscription_status column is display-only
(the live gate in whatsapp.send_message computes from plan_expires_on),
so a missed run never lets a suspended business keep sending.

Notices (sent to the OWNER via the platform channel, English, with a UPI
payment line when settings.operator_upi_id is set):
  - 5 days before expiry: renew soon
  - on entering grace:    expired, N days left
  - on suspension:        reminders paused, renew to resume
"""
from __future__ import annotations

import logging

from app.db import require_db
from app.models import PLAN_LABELS, PLAN_LIMITS, recommend_plan
from app.services import subscription as subs
from app.services import whatsapp
from app.services.license import active_debtor_count

log = logging.getLogger(__name__)


async def run() -> None:
    # Free pilot: everyone is active on Pro, so there is nothing to warn about or
    # suspend. Skip the whole pass (no renewal nagging during the pilot).
    if subs.free_pilot_active():
        log.info("Subscription check skipped - free pilot active")
        return
    db = require_db()
    biz_resp = (
        db.table("businesses")
        .select("id, business_name, plan, subscription_status, plan_expires_on")
        .execute()
    )

    for biz in biz_resp.data or []:
        try:
            stored = biz.get("subscription_status") or "trial"
            live = subs.effective_status(biz.get("plan_expires_on"))
            left = subs.days_left(biz.get("plan_expires_on"))
            name = biz.get("business_name") or "Your shop"
            pay = subs.renewal_payment_line(biz.get("plan"))
            tail = ("\n\n" + pay) if pay else ""

            # Free trial: on day 5 and day 7 name the plan this shop's debtor
            # count needs, with its price and the pay link (fires once each -
            # equality on days left).
            if stored == "trial" and left in (2, 0):
                n = active_debtor_count(db, biz["id"])
                rec = recommend_plan(n)
                price = PLAN_LIMITS[rec].get("price", 0)
                when = "today" if left == 0 else "in 2 days"
                plan_line = (f"your plan is {PLAN_LABELS[rec]} at Rs {price:,} a month "
                             f"(covers {PLAN_LIMITS[rec]['debtors']:,} debtors)"
                             if price else "message us for a Custom plan")
                rec_pay = subs.renewal_payment_line(rec.value)
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA free trial ends {when}. You have {n:,} active "
                    f"debtors, so {plan_line}." + (("\n\n" + rec_pay) if rec_pay else ""))
            # Paid plan: heads-up 7, 3 and 1 days before expiry.
            elif live in ("trial", "active") and left in (7, 3, 1):
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA plan ends in {left} day{'s' if left != 1 else ''}. "
                    f"Renew to keep reminders and bills running." + tail)

            if live == stored or (live == "active" and stored in ("trial", "active")):
                continue

            db.table("businesses").update({"subscription_status": live}).eq("id", biz["id"]).execute()

            if live == "grace":
                remaining = subs.GRACE_DAYS + (left or 0)
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA plan has expired. Renew within {remaining} "
                    f"day(s) or customer reminders will stop." + tail)
            elif live == "suspended":
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA plan is paused. Bills and reminders are NOT "
                    f"going out. They resume the moment you renew." + tail)
        except Exception:
            log.exception("Subscription check failed for %s", biz.get("id"))
