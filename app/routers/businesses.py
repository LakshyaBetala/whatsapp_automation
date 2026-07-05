"""Business registration and usage tracking.

Routers contain ZERO business logic — validate input, call service, return result.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.db import require_db
from app.models import PLAN_LIMITS, Plan

log = logging.getLogger(__name__)
router = APIRouter(tags=["businesses"])


# ── Request / response schemas ────────────────────────────────────────

class BusinessRegister(BaseModel):
    owner_name: str = Field(..., min_length=2, max_length=200)
    whatsapp_number: str = Field(..., description="Indian mobile, 10 digits (no country code) or 12 digits with 91 prefix")
    business_name: str | None = None
    plan: Plan = Plan.starter
    tally_company_name: str | None = None

    @field_validator("whatsapp_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        # Accept 10-digit or 91+10-digit
        if len(digits) == 10:
            digits = "91" + digits
        if len(digits) != 12 or not digits.startswith("91"):
            raise ValueError("Must be a valid Indian mobile number (10 digits or 91XXXXXXXXXX)")
        return digits


class BusinessResponse(BaseModel):
    id: str
    owner_name: str
    business_name: str | None
    whatsapp_number: str
    plan: str
    message: str
    agent_token: str | None = None


class UsageResponse(BaseModel):
    business_id: str
    message_count: int
    limit: int
    percent_used: float
    period_month: str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/register", response_model=BusinessResponse, status_code=201)
async def register_business(payload: BusinessRegister):
    """Onboard a new SMB owner. Returns the business_id the Tally agent needs."""
    db = require_db()

    # Duplicate check — same number must not register twice
    existing = (
        db.table("businesses")
        .select("id")
        .eq("whatsapp_number", payload.whatsapp_number)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Business already registered with this number.",
        )

    # Generate agent token
    agent_token = secrets.token_urlsafe(32)

    # Create business — 30-day subscription clock starts now
    from datetime import timedelta
    biz_resp = (
        db.table("businesses")
        .insert({
            "owner_name": payload.owner_name,
            "business_name": payload.business_name,
            "whatsapp_number": payload.whatsapp_number,
            "plan": payload.plan.value,
            "tally_company_name": payload.tally_company_name,
            "agent_token": agent_token,
            "subscription_status": "trial",
            "plan_expires_on": (date.today() + timedelta(days=30)).isoformat(),
        })
        .execute()
    )
    biz = biz_resp.data[0]

    # Create usage row for current month immediately
    today = date.today()
    period_month = today.replace(day=1)
    db.table("usage").insert({
        "business_id": biz["id"],
        "period_month": period_month.isoformat(),
        "message_count": 0,
    }).execute()

    return BusinessResponse(
        id=biz["id"],
        owner_name=biz["owner_name"],
        business_name=biz.get("business_name"),
        whatsapp_number=biz["whatsapp_number"],
        plan=biz["plan"],
        message="Save this token — it will not be shown again",
        agent_token=agent_token,
    )


@router.get("/{business_id}/usage", response_model=UsageResponse)
async def get_usage(business_id: str):
    """Return message usage vs plan limit for current month."""
    db = require_db()

    # Fetch business plan
    biz_resp = (
        db.table("businesses")
        .select("plan")
        .eq("id", business_id)
        .single()
        .execute()
    )
    if not biz_resp.data:
        raise HTTPException(status_code=404, detail="Business not found")

    plan = Plan(biz_resp.data["plan"])
    limit = PLAN_LIMITS[plan]["messages"]

    # Fetch current month usage
    today = date.today()
    period_month = today.replace(day=1)
    usage_resp = (
        db.table("usage")
        .select("message_count")
        .eq("business_id", business_id)
        .eq("period_month", period_month.isoformat())
        .limit(1)
        .execute()
    )
    count = usage_resp.data[0]["message_count"] if usage_resp.data else 0
    percent = round((count / limit) * 100, 1) if limit > 0 else 0

    return UsageResponse(
        business_id=business_id,
        message_count=count,
        limit=limit,
        percent_used=percent,
        period_month=period_month.isoformat(),
    )


@router.get("/{business_id}/qr")
async def get_whatsapp_qr(business_id: str):
    """Fetch the OpenWA login QR / connection status for the owner to scan.

    Proxies wa_service's /api/wa/status, which returns
    {"ready": bool, "qr": data-url-or-null}.
    """
    import httpx

    from app.config import settings

    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"{settings.openwa_url}/api/wa/status")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.error("Failed to fetch QR from OpenWA: %s", e)
        raise HTTPException(status_code=503, detail="WhatsApp service is currently unavailable.")
