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
from app.services import promises

log = logging.getLogger(__name__)

PULL_LIMIT = 15


def pull(db, business_id: str, limit: int = PULL_LIMIT) -> list[dict]:
    """The queued customer sends this shop should deliver right now.

    Returns [] outside shop hours, so a laptop that comes online at midnight
    delivers nothing until morning. Stale rows (older than EXPIRE_HOURS) are
    expired here rather than surprising a customer with a days-old reminder.

    Send-time promise re-check: a reminder can be queued by the sweep and then
    the customer promises to pay before the shop laptop delivers it (e.g. the
    laptop was off, so both the reply and the queued send land together on
    reconnect). We check the hold ONE more time here, at the moment of handing
    the message over, and quietly hold any reminder whose party is now on a
    promise pause - so we never chase someone who just promised."""
    if not within_send_window():
        return []
    rows = (db.table("wa_outbox")
            .select("id, payload, attempts, created_at, message_db_id")
            .eq("business_id", business_id).eq("status", "queued")
            .order("created_at").limit(max(1, min(50, limit))).execute()).data or []
    # Resolve each row's party + type so we only pause REMINDERS (never a bill).
    # Best-effort: if any of this fails, we deliver normally rather than block a
    # send - a re-check error must never stop a legitimate reminder.
    msg_by_id: dict = {}
    held: set = set()
    try:
        msg_ids = [r["message_db_id"] for r in rows if r.get("message_db_id")]
        if msg_ids:
            for m in (db.table("messages").select("id, client_id, type")
                      .in_("id", msg_ids).execute().data or []):
                msg_by_id[m["id"]] = m
        held = promises.held_now(db, [business_id]).get(business_id, set())
    except Exception:
        log.warning("outbox promise re-check skipped (delivering normally)", exc_info=True)
        msg_by_id, held = {}, set()

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
        m = msg_by_id.get(r.get("message_db_id"))
        if m and m.get("type") == "reminder" and m.get("client_id") in held:
            _hold(db, business_id, r["id"], r.get("message_db_id"))
            continue
        out.append({"id": r["id"], "payload": r["payload"],
                    "attempts": int(r.get("attempts") or 0)})
    return out


def _hold(db, business_id: str, row_id: str, message_db_id: str | None) -> None:
    """A queued reminder whose party promised to pay after it was queued: retire
    the row (status 'held' so it is not re-pulled) and mark the audit row. 'held'
    is not a failure, so it never inflates the failure-rate view."""
    try:
        db.table("wa_outbox").update(
            {"status": "held", "last_error": "paused: customer promised to pay"}
        ).eq("id", row_id).eq("business_id", business_id).execute()
        if message_db_id:
            db.table("messages").update({"delivery_status": "held"}).eq(
                "id", message_db_id).execute()
    except Exception:
        log.warning("Could not hold outbox row %s", row_id, exc_info=True)


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
