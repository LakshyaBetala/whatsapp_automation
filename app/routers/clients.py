"""Client CRUD + credit-days management.

Key rule: when credit_days changes, ALL open bills for that client get their
due_date recalculated.  Otherwise the reminder sweep fires early on existing bills.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.db import require_db
from app.models import Lang

log = logging.getLogger(__name__)
router = APIRouter(tags=["clients"])

VALID_CREDIT_DAYS = (30, 60, 90, 120, 180)


# ── Request / response schemas ────────────────────────────────────────

class ClientCreate(BaseModel):
    business_id: str
    name: str = Field(..., min_length=1, max_length=300)
    whatsapp_number: str | None = None
    language: Lang = Lang.hi
    credit_days: int = 30
    tally_ledger_name: str | None = None

    @field_validator("credit_days")
    @classmethod
    def validate_credit_days(cls, v: int) -> int:
        if v not in VALID_CREDIT_DAYS:
            raise ValueError(f"credit_days must be one of {VALID_CREDIT_DAYS}")
        return v

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, v):
        mapping = {"hindi": "hi", "gujarati": "gu", "marathi": "mr"}
        if isinstance(v, str):
            v = mapping.get(v.lower(), v.lower())
        return v


class ClientResponse(BaseModel):
    id: str
    business_id: str
    name: str
    whatsapp_number: str | None
    language: str
    credit_days: int
    reminders_enabled: bool


class ToggleResponse(BaseModel):
    client_id: str
    reminders_enabled: bool


class CreditUpdateResponse(BaseModel):
    client_id: str
    new_credit_days: int
    updated_bills_count: int
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/create", response_model=ClientResponse, status_code=201)
async def create_client(payload: ClientCreate):
    """Add a new client (debtor) for a business."""
    db = require_db()

    # Verify business exists
    biz_check = (
        db.table("businesses")
        .select("id")
        .eq("id", payload.business_id)
        .limit(1)
        .execute()
    )
    if not biz_check.data:
        raise HTTPException(status_code=404, detail="Business not found")

    resp = (
        db.table("clients")
        .insert({
            "business_id": payload.business_id,
            "name": payload.name,
            "whatsapp_number": payload.whatsapp_number,
            "language": payload.language.value,
            "credit_days": payload.credit_days,
            "tally_ledger_name": payload.tally_ledger_name,
        })
        .execute()
    )
    c = resp.data[0]
    return ClientResponse(
        id=c["id"],
        business_id=c["business_id"],
        name=c["name"],
        whatsapp_number=c.get("whatsapp_number"),
        language=c["language"],
        credit_days=c["credit_days"],
        reminders_enabled=c["reminders_enabled"],
    )


@router.put("/{client_id}/toggle", response_model=ToggleResponse)
async def toggle_reminders(client_id: str):
    """Flip reminders_enabled for a client."""
    db = require_db()

    current = (
        db.table("clients")
        .select("reminders_enabled")
        .eq("id", client_id)
        .single()
        .execute()
    )
    if not current.data:
        raise HTTPException(status_code=404, detail="Client not found")

    new_state = not current.data["reminders_enabled"]
    db.table("clients").update({"reminders_enabled": new_state}).eq("id", client_id).execute()

    return ToggleResponse(client_id=client_id, reminders_enabled=new_state)


@router.put("/{client_id}/credit", response_model=CreditUpdateResponse)
async def update_credit_days(client_id: str, credit_days: int):
    """Update credit period and recalculate due_date on ALL open bills.

    This is critical — if an owner upgrades a customer from 30-day to 90-day
    credit, all 5 open bills must get new due dates.  Otherwise the reminder
    sweep fires early on the old 30-day schedule.
    """
    if credit_days not in VALID_CREDIT_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"credit_days must be one of {VALID_CREDIT_DAYS}",
        )

    db = require_db()

    # Verify client exists
    client_resp = (
        db.table("clients")
        .select("id, business_id")
        .eq("id", client_id)
        .single()
        .execute()
    )
    if not client_resp.data:
        raise HTTPException(status_code=404, detail="Client not found")

    # Update credit_days on client
    db.table("clients").update({"credit_days": credit_days}).eq("id", client_id).execute()

    # Recalculate due_date on ALL open bills for this client
    open_bills = (
        db.table("bills")
        .select("id, invoice_date")
        .eq("client_id", client_id)
        .in_("status", ["pending", "partial", "overdue"])
        .execute()
    )

    updated_count = 0
    for bill in open_bills.data or []:
        from datetime import date as date_type
        invoice_date = date_type.fromisoformat(str(bill["invoice_date"]))
        new_due = invoice_date + timedelta(days=credit_days)
        db.table("bills").update({"due_date": new_due.isoformat()}).eq("id", bill["id"]).execute()
        updated_count += 1

    return CreditUpdateResponse(
        client_id=client_id,
        new_credit_days=credit_days,
        updated_bills_count=updated_count,
        message=f"Credit updated to {credit_days} days. {updated_count} open bill(s) due dates recalculated.",
    )
