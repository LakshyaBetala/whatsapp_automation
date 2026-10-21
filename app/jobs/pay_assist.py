"""Offer help when a customer tapped Pay and nothing arrived.

A tap on the pay page is intent, not money: the customer was handed to their
UPI app and nothing calls back. Most taps end in a payment, but some end in a
failed transaction, a daily limit, or a lost link, and the shop never hears
about it. ASVA waits ASSIST_AFTER_MIN, and if the tap is still unresolved it
sends that one customer a short, friendly offer of help.

Rules this job keeps, because it writes to a customer's WhatsApp:
  * once per tap, ever (assist_sent_at, stamped BEFORE the send so a crash or a
    second sweep cannot double-send), and at most one per party per day
  * only inside the shop's send window, and never for a suspended or dormant
    shop (the same guards the reminder sweep uses)
  * the wording never says the customer opened anything: it offers help with
    paying and says to ignore it if the payment is done. No amount, no bill,
    no second ask - the normal reminder cadence is what follows up
  * the shop can switch it off (businesses.pay_assist)
"""
from __future__ import annotations

import datetime as _dt
import logging

from app.config import settings
from app.db import get_client
from app.models import Lang, MessageType, Plan
from app.services import names, whatsapp

log = logging.getLogger(__name__)

ASSIST_AFTER_MIN = 15        # how long a tap may stay quiet before we offer help
ASSIST_MAX_AGE_MIN = 180     # older than this, the moment has passed - stay quiet


def _text(name: str, shop: str, en: bool) -> str:
    """One offer of help, worded so it never says what the customer did.

    The shop knows a link was opened; the customer should not be told they were
    watched. So this reads as ordinary service: if paying was a problem, here is
    help; if not, ignore it. No amount, no bill number, no "you clicked", no
    second ask."""
    who = names.clean_display(name) if name else ""
    if en:
        return (f"Dear {who},\n"
                f"If you had any trouble paying {shop}, reply here and we will send "
                "the bank details or a fresh payment link.\n\n"
                "If the payment is already done, please ignore this message.")
    return (f"Namaste {who} ji,\n"
            f"{shop} ka payment karne mein koi dikkat aayi ho to yahin reply karein, "
            "hum bank details ya naya link bhej denge.\n\n"
            "Payment ho chuka hai to is message ko ignore karein.")


def _due_taps(db, now: _dt.datetime) -> list[dict]:
    """Unresolved taps in the assist window that have not been helped yet."""
    lo = (now - _dt.timedelta(minutes=ASSIST_MAX_AGE_MIN)).isoformat()
    hi = (now - _dt.timedelta(minutes=ASSIST_AFTER_MIN)).isoformat()
    try:
        return (db.table("pay_link_events")
                .select("id, business_id, client_id, amount, created_at")
                .eq("kind", "tapped").is_("resolved_at", "null")
                .is_("assist_sent_at", "null")
                .gte("created_at", lo).lte("created_at", hi)
                .order("created_at").limit(50).execute()).data or []
    except Exception:
        log.debug("pay-assist: could not read taps (migration 059 applied?)", exc_info=True)
        return []


async def run() -> dict:
    """One sweep. Returns a small summary for the ops health view."""
    from app.services import license as lic
    from app.services import subscription as subs
    from app.jobs.outbox_sweep import within_send_window
    from app.services.batches import resolve_batch

    db = get_client()
    out = {"checked": 0, "sent": 0, "skipped": 0}
    if db is None:
        return out
    now = _dt.datetime.now(_dt.timezone.utc)
    taps = _due_taps(db, now)
    out["checked"] = len(taps)
    if not taps:
        return out

    if not within_send_window():
        out["skipped"] = len(taps)
        return out

    seen_today: set[str] = set()
    for tap in taps:
        bid, cid = tap.get("business_id"), tap.get("client_id")
        if not bid or not cid or cid in seen_today:
            out["skipped"] += 1
            continue
        try:
            b = (db.table("businesses")
                 .select("id, business_name, plan, plan_expires_on, msg_language, "
                         "reminder_batches, discount_pct, reminder_hour, last_seen, pay_assist")
                 .eq("id", bid).limit(1).execute()).data
            biz = b[0] if b else None
            if not biz or biz.get("pay_assist") is False:
                out["skipped"] += 1
                continue
            if (lic.is_dormant(biz)
                    or subs.live_status(biz.get("plan_expires_on")) == "suspended"):
                out["skipped"] += 1
                continue

            c = (db.table("clients")
                 .select("id, name, whatsapp_number, reminder_batch, excluded, reminders_enabled")
                 .eq("id", cid).limit(1).execute()).data
            client = c[0] if c else None
            if not client or not client.get("whatsapp_number") or client.get("excluded"):
                out["skipped"] += 1
                continue

            # Still owing? If a receipt already landed, the tap worked - say nothing.
            owed = (db.table("bills").select("id")
                    .eq("business_id", bid).eq("client_id", cid)
                    .in_("status", ["pending", "partial", "overdue"]).limit(1).execute()).data
            if not owed:
                out["skipped"] += 1
                continue

            # Stamp first: a send that fails must not leave this tap eligible
            # again on the next sweep - one offer of help is the whole point.
            db.table("pay_link_events").update(
                {"assist_sent_at": now.isoformat()}).eq("id", tap["id"]).execute()

            en = resolve_batch(biz, client.get("reminder_batch"))["lang"] == "english"
            shop = names.clean_business(biz.get("business_name") or "")
            await whatsapp.send_message(
                business_id=bid,
                to_number=client["whatsapp_number"],
                message_text=_text(client.get("name") or "", shop, en),
                plan=Plan(biz.get("plan") or "starter"),
                message_type=MessageType.reminder,
                client_id=cid,
                language=Lang.hi,
                template_name="pay_assist",
                channel="shop",
                priority=True,     # the moment is now; the window is already checked
            )
            seen_today.add(cid)
            out["sent"] += 1
        except Exception:
            log.exception("pay-assist failed for tap %s", tap.get("id"))
            out["skipped"] += 1
    if out["sent"]:
        log.info("pay-assist: offered help on %d quiet taps", out["sent"])
    return out
