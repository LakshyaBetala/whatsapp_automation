"""Payment application service - FIFO, oldest bill first.

Indian wholesale standard: when ₹15,000 arrives against a customer who owes
₹10,000 (30-day bill) and ₹25,000 (10-day bill), fully pay the ₹10,000 first,
then apply ₹5,000 to the ₹25,000 bill.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import whatsapp
from app.services.templates import inr, render

log = logging.getLogger(__name__)


async def apply_payment(
    *,
    business_id: str,
    client_id: str,
    amount: Decimal,
    source: str = "tally",
) -> dict:
    """Apply a payment to the client's outstanding bills using FIFO order.

    Args:
        business_id: UUID of the business.
        client_id: UUID of the client (debtor) who paid.
        amount: Total payment amount received.
        source: Where the payment was detected - ``"tally"``, ``"bot"``, or
                ``"customer_reply"``.

    Returns:
        Summary dict with bills affected, remaining balance, etc.
    """
    db = require_db()

    # Fetch open bills ordered by invoice_date ASC (FIFO)
    bills_resp = (
        db.table("bills")
        .select("id, invoice_number, amount, paid_amount, outstanding, status")
        .eq("business_id", business_id)
        .eq("client_id", client_id)
        .in_("status", ["pending", "partial", "overdue"])
        .order("invoice_date", desc=False)
        .execute()
    )
    open_bills = bills_resp.data or []

    if not open_bills:
        log.warning(
            "Payment of %s for client %s but no open bills found",
            amount,
            client_id,
        )
        return {
            "applied": False,
            "reason": "no_open_bills",
            "bills_affected": 0,
            "remaining_payment": float(amount),
        }

    remaining = amount
    bills_affected: list[dict] = []

    for bill in open_bills:
        if remaining <= 0:
            break

        bill_outstanding = Decimal(str(bill["outstanding"]))
        apply_amt = min(remaining, bill_outstanding)
        new_paid = Decimal(str(bill["paid_amount"])) + apply_amt
        remaining -= apply_amt

        # Determine new status
        bill_amount = Decimal(str(bill["amount"]))
        if new_paid >= bill_amount:
            new_status = "paid"
        elif new_paid > 0:
            new_status = "partial"
        else:
            new_status = bill["status"]

        db.table("bills").update({
            "paid_amount": float(new_paid),
            "status": new_status,
        }).eq("id", bill["id"]).execute()

        bills_affected.append({
            "bill_id": bill["id"],
            "invoice_number": bill["invoice_number"],
            "applied": float(apply_amt),
            "new_status": new_status,
        })

        log.info(
            "Applied %s to bill %s - status now %s",
            inr(apply_amt),
            bill["invoice_number"],
            new_status,
        )

    # ── Send payment confirmation to customer ─────────────────────────
    client_resp = (
        db.table("clients")
        .select("name, whatsapp_number, language")
        .eq("id", client_id)
        .single()
        .execute()
    )
    biz_resp = (
        db.table("businesses")
        .select("business_name, whatsapp_number, plan")
        .eq("id", business_id)
        .single()
        .execute()
    )
    client = client_resp.data
    biz = biz_resp.data

    if client and biz and client.get("whatsapp_number"):
        lang = Lang(client.get("language", "hi"))
        plan = Plan(biz["plan"])

        # Total remaining outstanding for this client
        remaining_resp = (
            db.table("bills")
            .select("outstanding")
            .eq("business_id", business_id)
            .eq("client_id", client_id)
            .in_("status", ["pending", "partial", "overdue"])
            .execute()
        )
        total_remaining = sum(
            Decimal(str(r["outstanding"])) for r in (remaining_resp.data or [])
        )

        tpl_name, body = render(
            "payment_confirmation", lang,
            client=client["name"],
            paid_amount=inr(amount),
            outstanding=inr(total_remaining),
        )

        await whatsapp.send_template(
            business_id=business_id,
            to_number=client["whatsapp_number"],
            campaign_name=tpl_name,
            template_params=[
                client["name"],
                inr(amount),
                inr(total_remaining),
            ],
            business_name=biz.get("business_name", ""),
            plan=plan,
            message_type=MessageType.payment_confirmation,
            client_id=client_id,
            language=lang,
            message_text=body,
        )

    # ── Notify owner ──────────────────────────────────────────────────
    if biz and client:
        alert = (
            f"{client['name']} ne {inr(amount)} payment kiya "
            f"({source}). {len(bills_affected)} bill(s) updated."
        )
        await whatsapp.notify_owner(business_id, alert)

    return {
        "applied": True,
        "source": source,
        "total_applied": float(amount - remaining),
        "remaining_payment": float(remaining),
        "bills_affected": len(bills_affected),
        "details": bills_affected,
    }
