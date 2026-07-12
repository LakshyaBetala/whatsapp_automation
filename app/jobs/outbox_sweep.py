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
  - stale (older than 48h): failed as 'expired' - a reminder from two days
    ago should not suddenly fire.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.db import require_db

log = logging.getLogger(__name__)

BATCH_LIMIT = 15          # max sends per run (a minute apart keeps pace human)
MAX_HARD_ATTEMPTS = 3     # permanent-error retries before giving up
EXPIRE_HOURS = 48


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True  # wa_service not running
    msg = str(exc).lower()
    return "503" in msg or "not ready" in msg  # service up, WhatsApp not linked


async def run() -> None:
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
