"""Central WhatsApp send service — using the OpenWA Node microservice.

The OpenWA service URL comes from settings (OPENWA_URL env var) so the
backend can run on Railway while the WA service runs elsewhere.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from app.config import settings
from app.db import require_db
from app.models import PLAN_LIMITS, Lang, MessageType, Plan

log = logging.getLogger(__name__)


async def _check_usage_and_increment(business_id: str, plan: Plan) -> bool:
    """Atomically check plan limit and increment usage counter."""
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
    if isinstance(data, list):
        data = data[0] if data else {}
    return bool(data.get("allowed", False))


async def send_message(
    *,
    business_id: str,
    to_number: str,
    message_text: str,
    plan: Plan = Plan.starter,
    message_type: MessageType = MessageType.invoice,
    reminder_day: Optional[int] = None,
    client_id: Optional[str] = None,
    bill_id: Optional[str] = None,
    language: Lang = Lang.hi,
    pdf_base64: Optional[str] = None,
    pdf_filename: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_filename: Optional[str] = None,
    template_name: str = "openwa_custom",
    channel: str = "shop",
) -> dict:
    """Send a WhatsApp message via OpenWA.

    channel: "shop" = the business's own number (customer-facing);
    "platform" = our company number (owner-facing: digest/alerts) —
    falls back to the shop session when PLATFORM_WA_URL is not set.

    Handles:
      1. Subscription gate (suspended businesses: only owner alerts)
      2. Atomic plan-limit check (Postgres ``FOR UPDATE``)
      3. OpenWA HTTP call
      4. ``messages`` table insert (audit)
    """
    db = require_db()

    # ── 0. Subscription gate — the server-side "license check" ────────
    from app.services import subscription as subs
    biz_row = (
        db.table("businesses")
        .select("plan_expires_on")
        .eq("id", business_id)
        .limit(1)
        .execute()
    )
    if biz_row.data:
        status = subs.effective_status(biz_row.data[0].get("plan_expires_on"))
        if status == "suspended" and message_type != MessageType.owner_alert:
            log.warning("Business %s suspended — send blocked", business_id)
            db.table("messages").insert({
                "business_id": business_id,
                "client_id": client_id,
                "bill_id": bill_id,
                "type": message_type.value,
                "reminder_day": reminder_day,
                "template_name": template_name,
                "language": language.value,
                "delivery_status": "suspended",
                "cost": 0,
            }).execute()
            return {"sent": False, "reason": "subscription_suspended"}

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
            "template_name": template_name,
            "language": language.value,
            "delivery_status": "limit_reached",
            "cost": 0,
        }).execute()
        return {"sent": False, "reason": "limit_reached"}

    # ── 2. OpenWA API call ───────────────────────────────────────────
    payload = {
        "phone": to_number,
        "message": message_text,
    }
    if pdf_base64:
        payload["pdf_base64"] = pdf_base64
        payload["pdf_name"] = pdf_filename or "invoice.pdf"
    elif image_base64:
        payload["media_base64"] = image_base64
        payload["media_type"] = "image/png"
        payload["media_name"] = image_filename or "qr.png"

    openwa_message_id = None
    delivery_status = "sent"

    base_url = settings.openwa_url
    if channel == "platform" and settings.platform_wa_url:
        base_url = settings.platform_wa_url

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(f"{base_url}/api/wa/send", json=payload)
            resp.raise_for_status()
            resp_data = resp.json()
            if not resp_data.get("success", True):
                raise RuntimeError(resp_data.get("error", "wa_service reported failure"))
            openwa_message_id = resp_data.get("messageId", "openwa-sent")
            log.info("WhatsApp sent to %s via OpenWA", to_number)
    except Exception as exc:
        log.error("OpenWA send failed for %s: %s", to_number, exc)
        delivery_status = "failed"

    # ── 3. Log to messages table ──────────────────────────────────────
    msg_row = (
        db.table("messages")
        .insert({
            "business_id": business_id,
            "client_id": client_id,
            "bill_id": bill_id,
            "type": message_type.value,
            "reminder_day": reminder_day,
            "template_name": template_name,
            "language": language.value,
            "aisensy_message_id": openwa_message_id, # Re-using this column for message ID
            "delivery_status": delivery_status,
            "cost": 0, # OpenWA is free!
        })
        .execute()
    )

    return {
        "sent": delivery_status == "sent",
        "message_id": openwa_message_id,
        "delivery_status": delivery_status,
        "db_id": msg_row.data[0]["id"] if msg_row.data else None,
    }

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
    message_text: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_filename: Optional[str] = None,
    channel: str = "shop",
) -> dict:
    """Template-shaped send used by jobs and routers.

    Callers render the message body locally via ``templates.render`` and pass
    it as ``message_text``; ``campaign_name``/``template_params`` are kept for
    the audit trail (and for a future BSP that sends by template name).
    If ``media_url`` points at a PDF, it is downloaded and attached; on any
    download failure the URL is appended to the text instead so the message
    still goes out.
    """
    text = message_text or "\n".join(str(p) for p in template_params)

    pdf_base64: Optional[str] = None
    if media_url:
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.get(media_url)
                resp.raise_for_status()
                pdf_base64 = base64.b64encode(resp.content).decode("ascii")
        except Exception as exc:
            log.warning("Could not fetch media %s (%s) — sending link instead", media_url, exc)
            if media_url not in text:
                text = f"{text}\n{media_url}"

    return await send_message(
        business_id=business_id,
        to_number=to_number,
        message_text=text,
        plan=plan,
        message_type=message_type,
        reminder_day=reminder_day,
        client_id=client_id,
        bill_id=bill_id,
        language=language,
        pdf_base64=pdf_base64,
        pdf_filename=media_filename,
        image_base64=image_base64,
        image_filename=image_filename,
        template_name=campaign_name,
        channel=channel,
    )


async def notify_owner(business_id: str, alert_text: str) -> dict:
    """Send a short alert to the business owner's personal WhatsApp."""
    db = require_db()
    biz = (
        db.table("businesses")
        .select("whatsapp_number, plan")
        .eq("id", business_id)
        .single()
        .execute()
    )
    if not biz.data:
        log.error("Business %s not found for owner notification", business_id)
        return {"sent": False, "reason": "business_not_found"}

    return await send_message(
        business_id=business_id,
        to_number=biz.data["whatsapp_number"],
        message_text=alert_text,
        plan=Plan(biz.data["plan"]),
        message_type=MessageType.owner_alert,
        language=Lang.hi,
        channel="platform",  # owner-facing → company number when configured
    )
