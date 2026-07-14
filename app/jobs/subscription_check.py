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
from app.services import subscription as subs
from app.services import whatsapp

log = logging.getLogger(__name__)


async def run() -> None:
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

            # Heads-up 5 days before expiry (fires exactly once - equality)
            if live in ("trial", "active") and left == 5:
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA plan ends in 5 days. Renew to keep "
                    f"reminders and bills running." + tail)

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
