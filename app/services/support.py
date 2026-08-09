"""Support/TEAM request log - the owner-to-operator channel, made visible.

When an owner sends "TEAM <message>", bot._forward_to_team both pings the product
team's WhatsApp AND records the request here so the Command Center can show every
request (who, when, text) and let the operator mark it resolved. Every call is
best-effort: a missing migration 035 degrades to "not logged", never an error, so
the owner's message still reaches the team.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def record(db, *, business_id: str | None, business_name: str | None,
           from_number: str | None, message: str) -> dict | None:
    """Log one TEAM/support request. Returns the row, or None if unavailable."""
    row = {
        "business_id": business_id,
        "business_name": business_name or "",
        "from_number": from_number or "",
        "message": (message or "")[:2000],
        "status": "open",
    }
    try:
        r = db.table("support_requests").insert(row).execute()
        return (r.data or [row])[0]
    except Exception:
        log.warning("support.record failed (apply migration 035?) - continuing")
        return None


def list_recent(db, limit: int = 50) -> list[dict]:
    """Support requests, open first then recently resolved, newest within each."""
    try:
        rows = (db.table("support_requests").select("*")
                .order("created_at", desc=True).limit(limit).execute()).data or []
    except Exception:
        return []
    rows.sort(key=lambda r: (r.get("status") != "open", ), reverse=False)
    return rows


def open_count(db) -> int:
    try:
        r = (db.table("support_requests").select("id", count="exact")
             .eq("status", "open").execute())
        return r.count or len(r.data or [])
    except Exception:
        return 0


def resolve(db, request_id: str, status: str = "resolved") -> bool:
    patch = {"status": status}
    if status == "resolved":
        patch["resolved_at"] = _now_iso()
    else:
        patch["resolved_at"] = None
    try:
        r = db.table("support_requests").update(patch).eq("id", request_id).execute()
        return bool(r.data)
    except Exception:
        log.exception("support.resolve failed for %s", request_id)
        return False
