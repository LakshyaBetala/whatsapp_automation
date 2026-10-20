"""Send the bill notices that are due (see app/services/bill_notices.py).

Every minute: each customer with due notices gets ONE message listing all of
their pending new bills, through the normal send path (subscription gate,
plan limits, outbox, ban-safe pacing; priority so the shop's evening send
window never holds a bill made at 20:00 - quiet hours are already applied by
send_after). Marked 'sent' once handed to the WhatsApp queue, 'skipped' with a
reason when it can never go (notices off, no number, suspended, limit), and
retried in 10 minutes on a transient failure.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

from app.db import get_client
from app.models import Lang, MessageType, Plan
from app.services import upi, whatsapp
from app.services.bill_notices import compose

log = logging.getLogger(__name__)

_FINAL_REASONS = {"limit_reached", "subscription_suspended", "not_on_whatsapp"}
RETRY = _dt.timedelta(minutes=10)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _outstanding(db, business_id: str, client_id: str):
    from app.services.bot import _client_outstanding
    return _client_outstanding(db, business_id, client_id)


async def _send_group(db, business_id: str, client_id: str) -> None:
    rows = (db.table("bill_notices").select("id, bill_id")
            .eq("business_id", business_id).eq("client_id", client_id)
            .eq("status", "pending").execute()).data or []
    if not rows:
        return
    ids = [r["id"] for r in rows]

    def mark(patch: dict) -> None:
        db.table("bill_notices").update(patch).in_("id", ids).execute()

    biz = ((db.table("businesses")
            .select("id, business_name, plan, bill_notices, upi_vpa, upi_vpa_2, upi_vpa_3, "
                    "bank_account_name, bank_account_no, bank_ifsc, bank_name, "
                    "reminder_batches, msg_language, discount_pct")
            .eq("id", business_id).limit(1).execute()).data or [None])[0]
    client = ((db.table("clients")
               .select("id, name, whatsapp_number, language, reminder_batch")
               .eq("id", client_id).limit(1).execute()).data or [None])[0]
    if not biz or biz.get("bill_notices") is False:
        return mark({"status": "skipped", "note": "bill notices off"})
    if not client or not client.get("whatsapp_number"):
        return mark({"status": "skipped", "note": "no WhatsApp number"})

    bills = (db.table("bills").select("id, invoice_number, invoice_date, due_date, amount")
             .in_("id", [r["bill_id"] for r in rows]).execute()).data or []
    if not bills:
        return mark({"status": "skipped", "note": "bills gone"})
    bills.sort(key=lambda b: (str(b.get("invoice_date") or ""), str(b.get("invoice_number") or "")))
    total = Decimal(str(_outstanding(db, business_id, client_id) or 0))

    from app.services.batches import batch_payment, resolve_batch
    from app.services.names import clean_business
    batch = resolve_batch(biz, client.get("reminder_batch"))
    pay = batch_payment(biz, batch)
    link = None
    if pay.get("primary") == "upi" and pay.get("vpa") and total > 0:
        ref = str(bills[0].get("invoice_number") or "") if len(bills) == 1 else f"{len(bills)} bills"
        link = upi.pay_page_url(business_id, total, ref, client_id=client_id)
    text = compose(client.get("name") or "Customer", clean_business(biz.get("business_name") or ""),
                   bills, total, en=batch.get("lang") == "english", pay_link=link)
    try:
        lang = Lang(client.get("language") or "hi")
    except ValueError:
        lang = Lang.hi
    try:
        plan = Plan(biz.get("plan") or "starter")
    except ValueError:
        plan = Plan.starter

    result = await whatsapp.send_message(
        business_id=business_id, to_number=client["whatsapp_number"], message_text=text,
        plan=plan, message_type=MessageType.invoice, client_id=client_id,
        bill_id=bills[0]["id"], language=lang, template_name="bill_notice", priority=True)
    if result.get("sent") or result.get("queued"):
        return mark({"status": "sent", "sent_at": _now().isoformat(),
                     "message_db_id": result.get("db_id")})
    reason = result.get("reason") or "send_failed"
    if reason in _FINAL_REASONS:
        return mark({"status": "skipped", "note": reason})
    mark({"send_after": (_now() + RETRY).isoformat()})      # transient: try again soon


async def run() -> None:
    db = get_client()
    if db is None:
        return
    try:
        due = (db.table("bill_notices").select("business_id, client_id")
               .eq("status", "pending").lte("send_after", _now().isoformat())
               .limit(500).execute()).data or []
    except Exception:
        log.debug("bill notices: read failed (migration 057?)", exc_info=True)
        return
    for biz_id, client_id in sorted({(r["business_id"], r["client_id"]) for r in due}):
        try:
            await _send_group(db, biz_id, client_id)
        except Exception:
            log.exception("bill notice send failed for %s/%s", biz_id, client_id)
