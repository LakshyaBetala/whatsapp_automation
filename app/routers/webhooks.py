"""Inbound WhatsApp webhook from AiSensy.

Two endpoints:
  GET  /webhooks/aisensy  - Meta verification handshake (required before AiSensy connects)
  POST /webhooks/aisensy  - receive inbound messages with dedup + always-200

Owners and customers reply with simple commands; AiSensy forwards them here.
We normalise the payload, then route to the bot command handler:
  LIST            -> full outstanding list (owner)
  STOP <name>     -> pause reminders for a client
  PAID <name>     -> mark a client's oldest open bill paid (owner)
  PAID            -> customer confirming their own payment
  <name> <amt> <date> -> create a bill (Phase 2 bot)

AiSensy's exact inbound JSON shape is account-specific, so payload parsing is
defensive and documented inline.

CRITICAL: POST webhook must ALWAYS return 200, even on internal errors.
AiSensy retries on non-200, causing duplicate processing.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db import require_db
from app.services import bot

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _extract(body: dict) -> tuple[str | None, str | None, str | None]:
    """Pull (from_number, text, message_id) out of an AiSensy inbound payload.

    AiSensy nests the message differently across plans; we probe the common
    shapes and fall back to None so a malformed call never 500s.
    """
    # Shape A: {"data": {"sender": "...", "message": "..."}}
    data = body.get("data") or body
    sender = (
        data.get("sender")
        or data.get("from")
        or data.get("mobile")
        or data.get("waId")
    )
    text = (
        data.get("message")
        or data.get("text")
        or (data.get("messageData") or {}).get("text")
    )
    if isinstance(text, dict):  # {"text": {"body": "..."}}
        text = text.get("body")

    # Message ID for dedup
    message_id = (
        data.get("messageId")
        or data.get("message_id")
        or body.get("messageId")
    )

    return sender, text, message_id


# ── GET: Meta verification handshake ──────────────────────────────────

@router.get("/aisensy")
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """Meta webhook verification handshake.

    When AiSensy (or Meta directly) registers a webhook, it sends a GET
    with hub.mode=subscribe, hub.verify_token=<your_token>, hub.challenge=<random>.
    We must return the challenge as plain text if the token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.webhook_verify_token:
        log.info("Webhook verification successful")
        return PlainTextResponse(content=hub_challenge or "")

    log.warning("Webhook verification failed: mode=%s", hub_mode)
    return PlainTextResponse(content="Forbidden", status_code=403)


# ── POST: Inbound message processing ─────────────────────────────────

@router.post("/aisensy")
async def aisensy_inbound(request: Request):
    """Receive and process an inbound WhatsApp message.

    ALWAYS returns 200 - even on internal errors. AiSensy retries on non-200,
    which causes duplicate processing. Errors are logged, never surfaced.
    """
    try:
        # Optional shared-secret check
        if settings.aisensy_webhook_secret:
            token = request.headers.get("x-webhook-secret") or request.query_params.get(
                "secret"
            )
            if token != settings.aisensy_webhook_secret:
                log.warning("Rejected webhook with bad secret")
                return {"ok": True}  # Still 200 - don't trigger retry

        body = await request.json()
        sender, text, message_id = _extract(body)
        data = body.get("data") or body
        media_b64 = data.get("media_base64")
        media_type = data.get("media_type") or "image/jpeg"

        if not sender or (not text and not media_b64):
            log.info("Ignoring webhook with no actionable message")
            return {"ok": True, "ignored": True}

        # ── Dedup: skip if we've already processed this messageId ─────
        if message_id:
            db = require_db()
            existing = (
                db.table("messages")
                .select("id", count="exact")
                .eq("aisensy_message_id", message_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                log.info("Duplicate webhook messageId=%s - skipping", message_id)
                return {"ok": True, "duplicate": True}

        reply = await bot.handle(
            sender.strip(), (text or "").strip(),
            media_b64=media_b64, media_type=media_type,
        )
        return {"ok": True, "reply": reply}

    except Exception:
        # Log but NEVER return non-200 - AiSensy will retry
        log.exception("Webhook processing error - returning 200 anyway")
        return {"ok": True, "error": "internal"}
