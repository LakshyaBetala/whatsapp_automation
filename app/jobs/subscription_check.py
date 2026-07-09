"""Daily subscription check - transitions statuses and warns owners.

Runs each morning. The stored subscription_status column is display-only
(the live gate in whatsapp.send_message computes from plan_expires_on),
so a missed run never lets a suspended business keep sending.

Notices (sent to the OWNER via the platform channel):
  - 5 days before expiry: "renew hone wala hai"
  - on entering grace:    "expire ho gaya - N din baaki"
  - on suspension:        "reminders band ho gaye - renew karein"
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
        .select("id, business_name, subscription_status, plan_expires_on")
        .execute()
    )

    for biz in biz_resp.data or []:
        try:
            stored = biz.get("subscription_status") or "trial"
            live = subs.effective_status(biz.get("plan_expires_on"))
            left = subs.days_left(biz.get("plan_expires_on"))
            name = biz.get("business_name") or ""

            # Heads-up 5 days before expiry (fires exactly once - equality)
            if live in ("trial", "active") and left == 5:
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: subscription 5 din mein khatam ho raha hai. "
                    f"Renew karein taaki reminders chalte rahein.")

            if live == stored or (live == "active" and stored in ("trial", "active")):
                continue

            db.table("businesses").update({"subscription_status": live}).eq("id", biz["id"]).execute()

            if live == "grace":
                remaining = subs.GRACE_DAYS + (left or 0)
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: subscription expire ho gaya. {remaining} din ke andar renew "
                    f"nahi kiya to customer reminders band ho jayenge.")
            elif live == "suspended":
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: subscription suspend ho gaya. Bills aur reminders ab NAHI ja "
                    f"rahe. Renew karte hi sab turant chalu ho jayega.")
        except Exception:
            log.exception("Subscription check failed for %s", biz.get("id"))
