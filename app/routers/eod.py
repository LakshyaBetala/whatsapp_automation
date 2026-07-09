"""EOD digest preview and manual trigger.

GET  /eod/{business_id}       - preview tonight's digest without sending
POST /eod/{business_id}/send  - trigger digest immediately (demos/testing)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.db import require_db
from app.jobs import eod_digest

log = logging.getLogger(__name__)
router = APIRouter(tags=["eod"])


@router.get("/{business_id}")
async def preview_digest(business_id: str):
    """Preview tonight's EOD digest without sending.

    Returns the formatted message text and all 5 metrics.
    """
    db = require_db()

    # Verify business exists
    biz_resp = (
        db.table("businesses")
        .select("id")
        .eq("id", business_id)
        .limit(1)
        .execute()
    )
    if not biz_resp.data:
        raise HTTPException(status_code=404, detail="Business not found")

    return await eod_digest.preview(business_id)


@router.post("/{business_id}/send")
async def send_digest(business_id: str):
    """Trigger EOD digest for a single business immediately.

    Used for demos and testing. In production, the scheduler fires at 9 PM IST.
    """
    db = require_db()

    biz_resp = (
        db.table("businesses")
        .select("id, business_name, whatsapp_number, plan, eod_enabled")
        .eq("id", business_id)
        .single()
        .execute()
    )
    if not biz_resp.data:
        raise HTTPException(status_code=404, detail="Business not found")

    biz = biz_resp.data

    # Build and send
    params = await eod_digest._build_digest(business_id, biz)
    if params is None:
        return {
            "sent": False,
            "reason": "All values are zero - no digest sent.",
        }

    from app.models import Lang, MessageType, Plan
    from app.services import whatsapp
    from app.services.templates import render

    tpl_name, rendered = render(
        "eod_digest",
        Lang.hi,
        business=params["business"],
        date=params["date"],
        bills_count=params["bills_count"],
        bills_total=params["bills_total"],
        payers_count=params["payers_count"],
        payments_total=params["payments_total"],
        outstanding_total=params["outstanding_total"],
        oldest_name=params["oldest_name"],
        oldest_amount=params["oldest_amount"],
        oldest_days=params["oldest_days"],
    )

    stale_warning = params.get("stale_warning", "")
    if stale_warning:
        rendered += stale_warning

    result = await whatsapp.send_template(
        business_id=business_id,
        to_number=biz["whatsapp_number"],
        campaign_name=tpl_name,
        template_params=params["_template_params"],
        business_name=params["business"],
        plan=Plan(biz["plan"]),
        message_type=MessageType.eod_digest,
        language=Lang.hi,
    )

    return {
        "sent": result.get("sent", False),
        "delivery_status": result.get("delivery_status"),
        "message_preview": rendered,
    }
