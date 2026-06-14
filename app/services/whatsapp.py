"""Central WhatsApp send service — every outbound message flows through here.

AiSensy API v2 — verify campaign names match exactly what is approved in dashboard.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings
from app.db import require_db
from app.models import PLAN_LIMITS, Lang, MessageType, Plan

log = logging.getLogger(__name__)

# AiSensy campaign send endpoint (API v2)
AISENSY_SEND_URL = f"{settings.aisensy_api_base}/campaign/t1/api/v2"


async def _check_usage_and_increment(business_id: str, plan: Plan) -> bool:
    """Atomically check plan limit and increment usage counter.

    Uses the Postgres ``increment_usage_if_allowed`` function which locks the
    row with ``FOR UPDATE`` — two concurrent sends at the boundary cannot both
    pass.  Returns ``True`` if the send is allowed.
    """
    db = require_db()
    limit = PLAN_LIMITS[plan]["messages"]
    result = db.rpc("increment_usage_if_allowed", {
        "p_business_id": business_id,
        "p_limit": limit,
    }).execute()

    if not result.data:
        log.error("increment_usage_if_allowed returned no data for %s", business_id)
        return False

    data = result.data
    # supabase-py may return a list with one element or a dict directly
    if isinstance(data, list):
        data = data[0] if data else {}
    return bool(data.get("allowed", False))


async def send_template(
    *,
    business_id: str,
    to_number: str,
    campaign_name: str,
    template_params: list[str],
    business_name: str = "",
    plan: Plan = Plan.starter,
    message_type: MessageType = MessageType.invoice,
    reminder_day: Optional[int] = None,
    client_id: Optional[str] = None,
    bill_id: Optional[str] = None,
    language: Lang = Lang.hi,
    media_url: Optional[str] = None,
    media_filename: Optional[str] = None,
) -> dict:
    """Send a WhatsApp template message via AiSensy.

    **All outbound WhatsApp messages MUST go through this function.**

    Handles:
      1. Atomic plan-limit check (Postgres ``FOR UPDATE``)
      2. AiSensy HTTP call (or dry-run log when not configured)
      3. ``messages`` table insert (audit + cost ledger)
    """
    db = require_db()

    # ── 1. Atomic plan-limit check ────────────────────────────────────
    allowed = await _check_usage_and_increment(business_id, plan)
    if not allowed:
        log.warning("Plan limit reached for business %s — skipping send", business_id)
        db.table("messages").insert({
            "business_id": business_id,
            "client_id": client_id,
            "bill_id": bill_id,
            "type": message_type.value,
            "reminder_day": reminder_day,
            "template_name": campaign_name,
            "language": language.value,
            "delivery_status": "limit_reached",
            "cost": 0,
        }).execute()
        return {"sent": False, "reason": "limit_reached"}

    # ── 2. AiSensy API call ───────────────────────────────────────────
    payload = {
        "apiKey": settings.aisensy_api_key,
        "campaignName": campaign_name,
        "destination": to_number,
        "userName": business_name,
        "templateParams": template_params,
    }
    if media_url:
        payload["media"] = {
            "url": media_url,
            "filename": media_filename or "document.pdf",
        }

    aisensy_message_id: str | None = None
    delivery_status = "sent"

    if settings.aisensy_configured:
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.post(AISENSY_SEND_URL, json=payload)
                resp.raise_for_status()
                resp_data = resp.json()
                aisensy_message_id = (
                    resp_data.get("messageId")
                    or resp_data.get("id")
                    or resp_data.get("data", {}).get("messageId")
                )
                log.info(
                    "WhatsApp sent to %s via AiSensy: %s",
                    to_number,
                    aisensy_message_id,
                )
        except httpx.HTTPError as exc:
            log.error("AiSensy send failed for %s: %s", to_number, exc)
            delivery_status = "failed"
    else:
        log.info(
            "[DRY RUN] WhatsApp to %s | campaign=%s | params=%s",
            to_number,
            campaign_name,
            template_params,
        )
        delivery_status = "dry_run"

    # ── 3. Log to messages table ──────────────────────────────────────
    msg_row = (
        db.table("messages")
        .insert({
            "business_id": business_id,
            "client_id": client_id,
            "bill_id": bill_id,
            "type": message_type.value,
            "reminder_day": reminder_day,
            "template_name": campaign_name,
            "language": language.value,
            "aisensy_message_id": aisensy_message_id,
            "delivery_status": delivery_status,
            "cost": 0.145 if delivery_status != "failed" else 0,
        })
        .execute()
    )

    return {
        "sent": delivery_status in ("sent", "dry_run"),
        "message_id": aisensy_message_id,
        "delivery_status": delivery_status,
        "db_id": msg_row.data[0]["id"] if msg_row.data else None,
    }


async def notify_owner(business_id: str, alert_text: str) -> dict:
    """Send a short alert to the business owner's personal WhatsApp."""
    db = require_db()
    biz = (
        db.table("businesses")
        .select("whatsapp_number, business_name, plan")
        .eq("id", business_id)
        .single()
        .execute()
    )
    if not biz.data:
        log.error("Business %s not found for owner notification", business_id)
        return {"sent": False, "reason": "business_not_found"}

    return await send_template(
        business_id=business_id,
        to_number=biz.data["whatsapp_number"],
        campaign_name="owner_alert_hi",
        template_params=[biz.data.get("business_name", ""), alert_text],
        business_name=biz.data.get("business_name", ""),
        plan=Plan(biz.data["plan"]),
        message_type=MessageType.owner_alert,
        language=Lang.hi,
    )
