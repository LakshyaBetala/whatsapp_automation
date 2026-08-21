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
import secrets as _secrets

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db import require_db, get_client
from app.services import bot
from app.services.bot import _match_row

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _secret_ok(supplied: str | None) -> bool:
    """Constant-time check of the shared inbound-webhook secret."""
    configured = (settings.aisensy_webhook_secret or "").strip()
    return bool(configured) and bool(supplied) and _secrets.compare_digest(
        supplied.strip(), configured)


def _token_is_a_business(agent_token: str | None) -> bool:
    """True if this agent_token belongs to a real paired shop. Authenticates that
    a legitimate shop install (not a random internet client) forwarded the inbound
    message, without shipping a shared fleet secret. Best-effort: DB down -> False."""
    tok = (agent_token or "").strip()
    if not tok:
        return False
    try:
        db = get_client()
        if db is None:
            return False
        r = (db.table("businesses").select("id").eq("agent_token", tok)
             .limit(1).execute()).data
        return bool(r)
    except Exception:
        log.debug("agent-token check failed", exc_info=True)
        return False


def _record_inbound(db, sender: str, message_id: str) -> None:
    """Persist the inbound messageId so the dedup check above actually works.

    Without this row, WhatsApp redelivering the same message (e.g. Baileys
    re-upserting history after a reconnect) would re-run the command - and a
    replayed PAID or BILL writes real data twice. Recorded AFTER successful
    handling on purpose: a crash mid-command SHOULD be reprocessed.
    Best-effort: never let bookkeeping break the webhook.
    """
    try:
        biz = _match_row(db, "businesses", "id", sender)
        business_id = biz["id"] if biz else None
        if not business_id:
            client = _match_row(db, "clients", "business_id", sender)
            business_id = client["business_id"] if client else None
        if not business_id:
            return  # unknown sender: bot ignored it anyway, nothing to replay
        db.table("messages").insert({
            "business_id": business_id,
            "type": "bot_reply",
            "template_name": "inbound",
            "aisensy_message_id": message_id,
            "delivery_status": "received",
            "cost": 0,
        }).execute()
    except Exception:
        log.warning("Could not record inbound messageId=%s", message_id, exc_info=True)


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


@router.get("/allowlist")
async def wa_allowlist(token: str = Query(default="")):
    """The shop's own customer numbers (last 10 digits) for the given agent token.

    Privacy: the shop's wa_service uses this to forward ONLY messages from its
    customers. Messages from anyone else - family, friends, unknown numbers -
    are then never sent to the server at all; they stay on the shop's laptop.
    This is the shop's own data going back to the shop's own machine.

    Best-effort: any error returns an empty list, and wa_service fails OPEN
    (keeps forwarding) so a hiccup never silently drops a real customer's reply.
    """
    from app.db import get_client
    from app.services import phones
    db = get_client()
    if db is None or not token:
        return {"numbers": []}
    try:
        biz = (db.table("businesses").select("id")
               .eq("agent_token", token).limit(1).execute()).data
        if not biz:
            return {"numbers": []}
        bid = biz[0]["id"]
        nums: set[str] = set()
        start = 0
        while True:
            rows = (db.table("clients").select("whatsapp_number")
                    .eq("business_id", bid).range(start, start + 999).execute()).data or []
            for r in rows:
                n = phones.last10(r.get("whatsapp_number") or "")
                if len(n) == 10:
                    nums.add(n)
            if len(rows) < 1000:
                break
            start += 1000
        return {"numbers": sorted(nums)}
    except Exception:
        log.debug("allowlist build failed", exc_info=True)
        return {"numbers": []}


# ── POST: Inbound message processing ─────────────────────────────────

@router.post("/aisensy")
async def aisensy_inbound(request: Request):
    """Receive and process an inbound WhatsApp message.

    ALWAYS returns 200 - even on internal errors. AiSensy retries on non-200,
    which causes duplicate processing. Errors are logged, never surfaced.
    """
    try:
        body = await request.json()
        sender, text, message_id = _extract(body)
        data = body.get("data") or body
        media_b64 = data.get("media_base64")
        media_type = data.get("media_type") or "image/jpeg"
        # Which number received this: "shop" (customer-facing) or "bot"
        # (owner-only ASVA assistant). Defaults to shop for backward compat.
        channel = data.get("channel") or "shop"

        # ── Authentication ────────────────────────────────────────────────
        # This endpoint is public (the BSP/wa_service POSTs here), so it MUST NOT
        # trust the client-supplied `channel`. Without this gate, anyone could POST
        # {sender:<a shop's number>, channel:"bot", message:"LIST"} and have the
        # OWNER command handler run + get the reply back = full impersonation.
        #   - bot channel (owner commands: LIST/PAID/BILL/STOP): the ASVA assistant
        #     number runs on OUR host, so it can carry the shared secret. REQUIRE it.
        #   - shop channel (silent customer capture): each shop's wa_service carries
        #     its own agent_token; require it to map to a real paired shop. Older
        #     builds that predate the token header are allowed through (logged) so
        #     customer capture never breaks mid-rollout - tightened once the fleet
        #     ships the token-sending wa_service.
        configured_secret = (settings.aisensy_webhook_secret or "").strip()
        secret_hdr = request.headers.get("x-webhook-secret") or request.query_params.get("secret")
        agent_tok = request.headers.get("x-agent-token")
        # A supplied-but-WRONG secret is always rejected (any channel) - it signals
        # tampering, never a legitimate caller.
        if configured_secret and secret_hdr is not None and not _secret_ok(secret_hdr):
            log.warning("Rejected inbound with a wrong webhook secret")
            return {"ok": True}                         # 200 so the BSP won't retry
        if channel == "bot":
            # Owner commands (LIST/PAID/BILL/STOP) are high-value: once a secret is
            # configured, require it. Our ASVA assistant number sends it; a stranger
            # cannot. Until it is configured on this server, allow but warn loudly.
            if configured_secret:
                if not _secret_ok(secret_hdr):
                    log.warning("Rejected BOT-channel inbound without a valid secret (sender=%s)", sender)
                    return {"ok": True}
            else:
                log.warning("BOT-channel inbound processed with NO webhook secret configured "
                            "- set AISENSY_WEBHOOK_SECRET to close owner-command impersonation")
        else:  # shop channel: prove a real paired shop forwarded it (once shops send it)
            if agent_tok is not None and not _token_is_a_business(agent_tok):
                log.warning("Rejected SHOP-channel inbound with an unknown agent token")
                return {"ok": True}

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
            media_b64=media_b64, media_type=media_type, channel=channel,
        )
        if message_id:
            _record_inbound(require_db(), sender.strip(), message_id)
        return {"ok": True, "reply": reply}

    except Exception:
        # Log but NEVER return non-200 - AiSensy will retry
        log.exception("Webhook processing error - returning 200 anyway")
        return {"ok": True, "error": "internal"}
