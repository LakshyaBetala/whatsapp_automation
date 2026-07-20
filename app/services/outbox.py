"""Thin-client outbox drain, over HTTP.

The shop app has NO database access - no service key on the shop is the entire
point of the thin client. So it cannot run the in-process ``outbox_sweep``.
Instead it authenticates with its agent_token, pulls its OWN queued customer
sends from the server, delivers each from the shop's WhatsApp (localhost:3001),
and acks the result. The server keeps everything that must not live on a shop:
the queue, the send window, the audit trail, and the store-forward-delete of
the invoice PDF. The shop is only the WhatsApp exit.

Reuses the exact send-window and cleanup rules the in-process sweep uses, so a
thin shop and a fat shop behave identically.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.jobs.outbox_sweep import EXPIRE_HOURS, _cleanup_pdf, within_send_window

log = logging.getLogger(__name__)

PULL_LIMIT = 15


def pull(db, business_id: str, limit: int = PULL_LIMIT) -> list[dict]:
    """The queued customer sends this shop should deliver right now.

    Returns [] outside shop hours, so a laptop that comes online at midnight
    delivers nothing until morning. Stale rows (older than EXPIRE_HOURS) are
    expired here rather than surprising a customer with a days-old reminder."""
    if not within_send_window():
        return []
    rows = (db.table("wa_outbox")
            .select("id, payload, attempts, created_at")
            .eq("business_id", business_id).eq("status", "queued")
            .order("created_at").limit(max(1, min(50, limit))).execute()).data or []
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        try:
            created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
            if now - created > timedelta(hours=EXPIRE_HOURS):
                _mark(db, business_id, r["id"], "failed", int(r.get("attempts") or 0), "expired")
                continue
        except (TypeError, ValueError):
            pass
        out.append({"id": r["id"], "payload": r["payload"],
                    "attempts": int(r.get("attempts") or 0)})
    return out


def ack(db, business_id: str, row_id: str, status: str,
        attempts: int = 1, error: str | None = None) -> bool:
    """Record what happened to a delivery the shop attempted. Scoped to the
    caller's business_id, so a shop can only ever ack its OWN rows."""
    if status not in ("sent", "failed", "queued"):
        status = "failed"
    return _mark(db, business_id, row_id, status, max(0, int(attempts)), error)


def _mark(db, business_id: str, row_id: str, status: str,
          attempts: int, error: str | None) -> bool:
    patch: dict = {"status": status, "attempts": attempts, "last_error": error}
    if status == "sent":
        patch["sent_at"] = datetime.now(timezone.utc).isoformat()
    # The business_id filter is the security boundary: a token can only touch
    # rows belonging to its own business.
    updated = (db.table("wa_outbox").update(patch)
               .eq("id", row_id).eq("business_id", business_id).execute()).data
    if not updated:
        return False
    row = updated[0]
    if status in ("sent", "failed") and row.get("message_db_id"):
        try:
            db.table("messages").update({"delivery_status": status}).eq(
                "id", row["message_db_id"]).execute()
        except Exception:
            log.warning("Could not mirror outbox %s to messages", row_id)
    if status == "sent" and row.get("message_db_id"):
        _cleanup_pdf(db, row["message_db_id"])
    return True
