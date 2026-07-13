"""Bill creation and listing.

POST /bills/create returns immediately - PDF generation and WhatsApp delivery
run in a FastAPI BackgroundTask (1-3 seconds).  Response returns in <200ms.

GET /bills/{business_id} returns bills with client name and computed days_since_due.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import pdf as pdf_service
from app.services import whatsapp
from app.services.templates import inr, render

log = logging.getLogger(__name__)
router = APIRouter(tags=["bills"])


# ── Request / response schemas ────────────────────────────────────────

class BillCreate(BaseModel):
    business_id: str
    client_id: str
    amount: Decimal = Field(..., gt=0)
    invoice_date: date
    description: str = "Goods as per invoice"
    invoice_number: str | None = None


class BillCreateResponse(BaseModel):
    id: str
    invoice_number: str | None
    amount: float
    due_date: str
    status: str
    message: str


class BillListItem(BaseModel):
    id: str
    invoice_number: str | None
    client_name: str
    amount: float
    paid_amount: float
    outstanding: float
    status: str
    invoice_date: str
    due_date: str | None
    days_since_due: int | None
    pdf_url: str | None


# ── Background task: PDF + WhatsApp ───────────────────────────────────

async def _generate_and_deliver(
    bill_id: str,
    pdf_base64: str | None = None,
    pdf_filename: str | None = None,
) -> None:
    """Background task: deliver a bill to the customer on WhatsApp.

    If ``pdf_base64`` is given (Tally's OWN exported invoice, already in hand),
    the exact bytes are sent DIRECTLY - no Supabase Storage upload/re-download
    round-trip, so a storage hiccup can never drop the bill. Otherwise we reuse
    the stored ``pdf_url`` or generate our own PDF.

    Runs after the API has already returned to the caller. Any failure is
    logged but never bubbles up.
    """
    db = require_db()

    # Fetch bill + client + business
    bill_resp = (
        db.table("bills")
        .select("*, clients(id, name, whatsapp_number, language, credit_days), businesses:business_id(id, business_name, whatsapp_number, plan, upi_vpa)")
        .eq("id", bill_id)
        .single()
        .execute()
    )
    if not bill_resp.data:
        log.error("Background task: bill %s not found", bill_id)
        return

    bill = bill_resp.data
    client = bill.get("clients") or {}
    biz = bill.get("businesses") or {}

    # Calculate previous outstanding for this client (excluding this bill)
    prev_resp = (
        db.table("bills")
        .select("outstanding")
        .eq("business_id", bill["business_id"])
        .eq("client_id", bill["client_id"])
        .in_("status", ["pending", "partial", "overdue"])
        .neq("id", bill_id)
        .execute()
    )
    previous_outstanding = sum(
        Decimal(str(b["outstanding"])) for b in (prev_resp.data or [])
    )

    # ── 1. PDF ────────────────────────────────────────────────────────
    # Prefer Tally's OWN exported invoice PDF (the exact GST tax-invoice, which
    # ASVA cannot recreate). If the caller handed us the base64 bytes, we send
    # those directly. Otherwise reuse the stored pdf_url or generate our own.
    pdf_url = bill.get("pdf_url")
    have_direct = bool(pdf_base64)
    if not have_direct and not pdf_url:
        try:
            pdf_url = await pdf_service.generate_invoice_pdf(
                business_name=biz.get("business_name", ""),
                business_phone=biz.get("whatsapp_number", ""),
                upi_vpa=biz.get("upi_vpa"),
                client_name=client.get("name", "Customer"),
                invoice_number=bill.get("invoice_number") or bill_id[:8],
                invoice_date=date.fromisoformat(str(bill["invoice_date"])),
                due_date=date.fromisoformat(str(bill["due_date"])) if bill.get("due_date") else date.today(),
                credit_days=client.get("credit_days", 30),
                amount=Decimal(str(bill["amount"])),
                previous_outstanding=previous_outstanding,
                description="Goods as per invoice",
                bill_id=bill_id,
            )
            db.table("bills").update({"pdf_url": pdf_url}).eq("id", bill_id).execute()
            log.info("PDF generated for bill %s: %s", bill_id, pdf_url)
        except Exception:
            log.exception("PDF generation failed for bill %s", bill_id)

    # ── 2. Send WhatsApp ──────────────────────────────────────────────
    client_phone = client.get("whatsapp_number")
    if not client_phone:
        log.info("No WhatsApp number for client %s - skipping delivery", client.get("name"))
        return

    try:
        lang = Lang(client.get("language") or "hi")
        plan = Plan(biz.get("plan", "starter"))
        invoice_num = bill.get("invoice_number") or bill_id[:8]
        amount_str = inr(Decimal(str(bill["amount"])))

        tpl_name, body = render(
            "invoice", lang,
            client=client.get("name", "Customer"),
            business=biz.get("business_name", ""),
            invoice_number=invoice_num,
            amount=amount_str,
            date=date.fromisoformat(str(bill["invoice_date"])).strftime("%d-%m-%Y"),
            upi_link=biz.get("upi_vpa") or "",
            pdf_url="",   # never dump a storage URL into the message body
        )

        if have_direct:
            # Send the exact PDF bytes we already hold - most robust path.
            await whatsapp.send_message(
                business_id=bill["business_id"],
                to_number=client_phone,
                message_text=body,
                plan=plan,
                message_type=MessageType.invoice,
                client_id=client.get("id"),
                bill_id=bill_id,
                language=lang,
                pdf_base64=pdf_base64,
                pdf_filename=pdf_filename or f"Invoice_{invoice_num}.pdf",
                template_name=tpl_name,
            )
        else:
            await whatsapp.send_template(
                business_id=bill["business_id"],
                to_number=client_phone,
                campaign_name=tpl_name,
                template_params=[
                    client.get("name", "Customer"),
                    biz.get("business_name", ""),
                    invoice_num,
                    amount_str,
                    date.fromisoformat(str(bill["invoice_date"])).strftime("%d-%m-%Y"),
                ],
                business_name=biz.get("business_name", ""),
                plan=plan,
                message_type=MessageType.invoice,
                client_id=client.get("id"),
                bill_id=bill_id,
                language=lang,
                media_url=pdf_url,
                media_filename=f"Invoice_{invoice_num}.pdf",
                message_text=body,
            )
    except Exception:
        log.exception("WhatsApp delivery failed for bill %s", bill_id)


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/create", response_model=BillCreateResponse, status_code=201)
async def create_bill(payload: BillCreate, background_tasks: BackgroundTasks):
    """Create a bill manually (non-Tally flow).

    Auto-calculates due_date from client's credit_days.
    Returns immediately - PDF generation + WhatsApp delivery run in background.
    """
    db = require_db()

    # Fetch client credit_days for due_date calculation
    client_resp = (
        db.table("clients")
        .select("id, credit_days")
        .eq("id", payload.client_id)
        .single()
        .execute()
    )
    if not client_resp.data:
        raise HTTPException(status_code=404, detail="Client not found")

    # Verify business exists
    biz_resp = (
        db.table("businesses")
        .select("id")
        .eq("id", payload.business_id)
        .single()
        .execute()
    )
    if not biz_resp.data:
        raise HTTPException(status_code=404, detail="Business not found")

    # Auto-calculate due_date
    credit_days = client_resp.data["credit_days"]
    due_date = payload.invoice_date + timedelta(days=credit_days)

    # Create bill - do NOT set outstanding (GENERATED ALWAYS column)
    bill_data = {
        "business_id": payload.business_id,
        "client_id": payload.client_id,
        "invoice_number": payload.invoice_number,
        "amount": float(payload.amount),
        "paid_amount": 0,
        "status": "pending",
        "invoice_date": payload.invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
    }
    bill_resp = db.table("bills").insert(bill_data).execute()
    bill = bill_resp.data[0]

    # PDF + WhatsApp in background - API returns in <200ms
    background_tasks.add_task(_generate_and_deliver, bill["id"])

    return BillCreateResponse(
        id=bill["id"],
        invoice_number=payload.invoice_number,
        amount=float(payload.amount),
        due_date=due_date.isoformat(),
        status="created",
        message="Bill created. PDF generation and WhatsApp delivery queued.",
    )


@router.get("/{business_id}", response_model=list[BillListItem])
async def list_bills(
    business_id: str,
    status: Optional[str] = Query(default=None, description="Filter: pending, partial, paid, overdue"),
    client_id: Optional[str] = Query(default=None, description="Filter by client UUID"),
):
    """List all bills for a business with client names and computed days_since_due."""
    db = require_db()
    today = date.today()

    query = (
        db.table("bills")
        .select("id, invoice_number, amount, paid_amount, outstanding, status, invoice_date, due_date, pdf_url, client_id, clients(name)")
        .eq("business_id", business_id)
        .order("invoice_date", desc=True)
    )

    if status:
        query = query.eq("status", status)
    if client_id:
        query = query.eq("client_id", client_id)

    resp = query.execute()

    items = []
    for bill in resp.data or []:
        due_str = bill.get("due_date")
        days_since_due = None
        if due_str:
            due = date.fromisoformat(str(due_str))
            days_since_due = (today - due).days

        items.append(BillListItem(
            id=bill["id"],
            invoice_number=bill.get("invoice_number"),
            client_name=(bill.get("clients") or {}).get("name", "Unknown"),
            amount=float(bill["amount"]),
            paid_amount=float(bill["paid_amount"]),
            outstanding=float(bill["outstanding"]),
            status=bill["status"],
            invoice_date=str(bill["invoice_date"]),
            due_date=due_str,
            days_since_due=days_since_due,
            pdf_url=bill.get("pdf_url"),
        ))

    return items
