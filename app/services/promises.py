"""Promise-to-Pay state service.

A thin, well-bounded layer over the payment_promises table (migration 028).
Every read/write TOLERATES the table being absent (migration 028 not yet
applied) exactly like checkpoint.py tolerates migration 027: a missing table
degrades to "no holds", never an exception, so the sweep and bot never break on
an un-migrated DB and the feature simply stays off until the migration lands.

Invariant enforced here: at most one status='open' row per (business_id,
client_id). create() supersedes any existing open row before inserting.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger(__name__)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def create(db, business_id: str, client_id: str, *, kind: str,
           hold_until: _dt.datetime, amount=None, promise_date=None,
           raw_text: str | None = None, source: str = "text",
           confidence: float | None = None) -> dict | None:
    """Record a new OPEN promise/claim, superseding any existing open one for
    this client. Returns the inserted row, or None if the table is missing."""
    try:
        # Supersede the previous open row so exactly one stays live per client.
        (db.table("payment_promises")
         .update({"status": "superseded", "updated_at": _now_iso()})
         .eq("business_id", business_id).eq("client_id", client_id)
         .eq("status", "open").execute())
        row = {
            "business_id": business_id,
            "client_id": client_id,
            "kind": kind,
            "amount": float(amount) if amount not in (None, "") else None,
            "promise_date": str(promise_date)[:10] if promise_date else None,
            "hold_until": hold_until.isoformat(),
            "status": "open",
            "raw_text": (raw_text or "")[:2000] or None,
            "source": source,
            "confidence": float(confidence) if confidence is not None else None,
        }
        res = db.table("payment_promises").insert(row).execute()
        return (res.data or [row])[0]
    except Exception:
        log.warning("promises.create failed (apply migration 028?) - continuing")
        return None


def held_now(db, business_ids: list[str]) -> dict[str, set]:
    """{business_id: set(client_id)} for clients with an OPEN hold that is still
    active (hold_until in the future). One query; tolerant of a missing table
    (returns {}). This is the gate the reminder sweep consults - a held party is
    skipped, so a promise beats the cadence while it is live."""
    if not business_ids:
        return {}
    try:
        rows = (db.table("payment_promises")
                .select("business_id, client_id")
                .in_("business_id", business_ids)
                .eq("status", "open")
                .gt("hold_until", _now_iso())
                .execute()).data or []
    except Exception:
        return {}
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r["business_id"], set()).add(r["client_id"])
    return out


def find_open(db, business_id: str, client_id: str) -> dict | None:
    """The client's current open promise, or None."""
    try:
        r = (db.table("payment_promises").select("*")
             .eq("business_id", business_id).eq("client_id", client_id)
             .eq("status", "open").order("created_at", desc=True)
             .limit(1).execute()).data
        return r[0] if r else None
    except Exception:
        return None


def open_for_business(db, business_id: str) -> list[dict]:
    """All open promises for a business (for the owner's PROMISES list), newest
    first. Tolerant of a missing table (returns [])."""
    try:
        return (db.table("payment_promises").select("*")
                .eq("business_id", business_id).eq("status", "open")
                .order("hold_until").execute()).data or []
    except Exception:
        return []


def close_for_client(db, business_id: str, client_id: str, status: str) -> bool:
    """Move this client's open promise to a terminal status: 'cancelled' (owner
    said CHASE) or 'kept' (owner marked PAID). Returns True if a row changed."""
    try:
        res = (db.table("payment_promises")
               .update({"status": status, "updated_at": _now_iso()})
               .eq("business_id", business_id).eq("client_id", client_id)
               .eq("status", "open").execute())
        return bool(res.data)
    except Exception:
        return False


def due_followups(db) -> list[dict]:
    """Open promises whose hold_until has passed and that have not been followed
    up yet - the date-arrived pass processes these. Tolerant (returns [])."""
    try:
        return (db.table("payment_promises").select("*")
                .eq("status", "open")
                .lte("hold_until", _now_iso())
                .is_("followup_sent_at", "null")
                .execute()).data or []
    except Exception:
        return []


def mark(db, promise_id: str, status: str, *, followup: bool = False) -> bool:
    """Set a promise's terminal status ('kept' | 'broken'); stamp followup_sent_at
    when followup=True so the date-arrived pass never processes it twice."""
    try:
        patch = {"status": status, "updated_at": _now_iso()}
        if followup:
            patch["followup_sent_at"] = _now_iso()
        res = db.table("payment_promises").update(patch).eq("id", promise_id).execute()
        return bool(res.data)
    except Exception:
        return False
