"""Outbox sweep - delivers queued WhatsApp sends from the SHOP number.

Runs every minute on the shop deployment (ENABLE_OUTBOX_SEND=true). The bot
laptop queues customer-facing sends into ``wa_outbox`` (it must never message
a party from the bot number); this job POSTs each payload to the local
wa_service (the shop's own WhatsApp) with human-like pacing, then marks the
outbox row and its ``messages`` audit row sent/failed.

Failure policy:
  - transient (service down / WhatsApp not connected): stays queued, retried
    next minute - the send simply waits for the shop laptop to come online.
  - permanent (bad payload, number not on WhatsApp): failed after 3 attempts.
  - stale (older than EXPIRE_HOURS): failed as 'expired' - a reminder from days
    ago should not suddenly fire.

Customer-facing sends also obey a send window (see ``within_send_window``): the
queue only drains during shop hours, so a laptop switched on at midnight cannot
blast the day's backlog. Owner-facing sends (digest, alerts, the "ASVA was not
running" catch-up nudge) never enter this queue - they go direct on the bot
number - so the owner is still reached at any hour.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.db import require_db
from app.services import pdf as pdf_service

log = logging.getLogger(__name__)

BATCH_LIMIT = 15          # max sends per run (a minute apart keeps pace human)
MAX_HARD_ATTEMPTS = 3     # permanent-error retries before giving up
# Long enough to survive the send window plus a closed weekend (a Friday-evening
# queue reaching Monday morning is ~62h), short enough that nothing fires days
# later as a surprise.
EXPIRE_HOURS = 72


def within_send_window(now: datetime | None = None) -> bool:
    """May customer-facing queued sends leave right now?

    The queue waits while the shop laptop is off, so a laptop switched on late
    at night would otherwise deliver the whole day's reminders at once. Holding
    them until shop hours protects the customer relationship AND the shop's
    WhatsApp number (midnight bursts are a classic ban trigger)."""
    if not settings.enforce_send_window:
        return True
    tz = ZoneInfo(settings.timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    return settings.send_window_start_hour <= now.hour < settings.send_window_end_hour


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True  # wa_service not running
    msg = str(exc).lower()
    return "503" in msg or "not ready" in msg  # service up, WhatsApp not linked


async def run() -> None:
    if not within_send_window():
        return          # outside shop hours - the queue simply waits
    db = require_db()
    rows = (
        db.table("wa_outbox")
        .select("id, business_id, message_db_id, payload, attempts, created_at")
        .eq("status", "queued")
        .order("created_at")
        .limit(BATCH_LIMIT)
        .execute()
    ).data or []
    if not rows:
        return

    now = datetime.now(timezone.utc)
    sent = kept = failed = 0

    for i, r in enumerate(rows):
        # Expire ancient items instead of surprising a party days later.
        try:
            created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
            if now - created > timedelta(hours=EXPIRE_HOURS):
                _mark(db, r, "failed", r["attempts"], "expired")
                failed += 1
                continue
        except (TypeError, ValueError):
            pass

        # Human-like gap between actual sends (bursts trigger WhatsApp bans).
        if sent > 0:
            await asyncio.sleep(random.uniform(settings.send_gap_min_s, settings.send_gap_max_s))

        try:
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.post(f"{settings.openwa_url}/api/wa/send", json=r["payload"])
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success", True):
                    raise RuntimeError(data.get("error", "wa_service reported failure"))
            _mark(db, r, "sent", r["attempts"] + 1, None)
            sent += 1
        except Exception as exc:
            attempts = r["attempts"] + 1
            if _is_transient(exc):
                # Shop WhatsApp offline: keep the whole queue for the next run.
                _mark(db, r, "queued", attempts, str(exc)[:300])
                kept += 1
                log.info("Outbox: shop WhatsApp unavailable (%s) - %d sends waiting", exc, len(rows) - i)
                break
            status = "queued" if attempts < MAX_HARD_ATTEMPTS else "failed"
            _mark(db, r, status, attempts, str(exc)[:300])
            failed += status == "failed"
            kept += status == "queued"

    if sent or failed:
        log.info("Outbox sweep: sent=%d, waiting=%d, failed=%d", sent, kept, failed)


def _mark(db, row: dict, status: str, attempts: int, error: str | None) -> None:
    patch: dict = {"status": status, "attempts": attempts, "last_error": error}
    if status == "sent":
        patch["sent_at"] = datetime.now(timezone.utc).isoformat()
    db.table("wa_outbox").update(patch).eq("id", row["id"]).execute()
    # Mirror final state onto the messages audit row.
    if status in ("sent", "failed") and row.get("message_db_id"):
        try:
            db.table("messages").update({"delivery_status": status}).eq(
                "id", row["message_db_id"]).execute()
        except Exception:
            log.warning("Could not update messages row for outbox %s", row["id"])
    if status == "sent" and row.get("message_db_id"):
        _cleanup_pdf(db, row["message_db_id"])


def _cleanup_pdf(db, message_db_id: str) -> None:
    """Store-forward-delete: the bill has now actually reached the customer, so
    drop the stored PDF. We hold Tally's exported invoice only long enough to
    deliver it - no customer's bill lingers in the bucket afterwards.
    Best-effort; a cleanup failure can never affect an already-sent bill."""
    try:
        m = (db.table("messages").select("bill_id")
             .eq("id", message_db_id).limit(1).execute()).data
        bill_id = m[0].get("bill_id") if m else None
        if not bill_id:
            return
        b = (db.table("bills").select("invoice_number")
             .eq("id", bill_id).limit(1).execute()).data
        invoice_number = (b[0].get("invoice_number") if b else None) or str(bill_id)[:8]
        pdf_service.delete_pdf(bill_id, invoice_number)
    except Exception:
        log.warning("PDF cleanup skipped for message %s", message_db_id)
