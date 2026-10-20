"""Remember what customers say.

Every inbound customer reply is stored (best-effort) so two things become
possible: the owner can see a party's story on the tracker ("reminded 3 times,
replied once, went quiet"), and the payment-behaviour dataset - our long-term
moat - accrues from day one. Writes never break the reply flow: any failure is
swallowed, because losing one stored line must never stop a reminder or a nudge.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def record(db, business_id: str, client_id: str | None, body: str, *,
           intent: str | None = None, from_number: str | None = None) -> str | None:
    """Store one inbound message and return its row id. Best-effort; a missing
    table or a write error is logged at debug and returns None (the caller's
    reply must still go out)."""
    if not business_id or not (body or "").strip():
        return None
    try:
        r = db.table("inbound_messages").insert({
            "business_id": business_id,
            "client_id": client_id,
            "from_number": (from_number or "")[:20] or None,
            "body": body[:1000],
            "intent": intent,
        }).execute()
        return ((r.data or [{}])[0] or {}).get("id")
    except Exception:
        log.debug("inbound record skipped (business %s)", business_id, exc_info=True)
        return None


def set_intent(db, row_id: str | None, intent: str | None) -> None:
    """Fill in what the reply turned out to mean (rules or model), once known.
    Best-effort, like record()."""
    if not (row_id and intent):
        return
    try:
        db.table("inbound_messages").update({"intent": intent}).eq("id", row_id).execute()
    except Exception:
        log.debug("inbound intent update skipped (%s)", row_id, exc_info=True)


def recent_for_client(db, business_id: str, client_id: str, limit: int = 20) -> list[dict]:
    """A party's recent inbound messages, newest first. Powers the tracker /
    party page. Best-effort: returns [] on any error."""
    if not (business_id and client_id):
        return []
    try:
        r = (db.table("inbound_messages")
             .select("body, intent, created_at")
             .eq("business_id", business_id).eq("client_id", client_id)
             .order("created_at", desc=True).limit(limit).execute())
        return r.data or []
    except Exception:
        log.debug("inbound read failed (business %s)", business_id, exc_info=True)
        return []
