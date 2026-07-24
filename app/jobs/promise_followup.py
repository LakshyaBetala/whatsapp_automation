"""Promise-to-Pay follow-up: the date-arrived pass. Runs hourly.

For each OPEN promise whose hold_until has passed:
  - if the client has no open bills left (a Tally receipt cleared them) -> KEPT,
    quietly. The loop closed on its own.
  - else -> BROKEN: flip the status off 'open' (which releases the hold, so the
    next reminder sweep resumes the single latest cadence reminder via
    latest_reached_point - no backlog, no double message), stamp followup_sent_at
    so this promise is never processed twice, and send the owner a one-liner.

ASVA never messages the customer here; releasing the hold lets the normal sweep
do the one catch-up send. Tolerant of a missing migration 028 (due_followups
returns [] -> nothing to do).
"""
from __future__ import annotations

import logging

from app.config import settings
from app.db import require_db
from app.services import promises, whatsapp

log = logging.getLogger(__name__)


def _has_open_bills(db, business_id: str, client_id: str) -> bool:
    """True if the client still has any pending/partial/overdue bill. Best-effort;
    on a query error assume unpaid (safer: we resume reminders rather than
    silently mark a promise kept)."""
    try:
        r = (db.table("bills").select("id", count="exact")
             .eq("business_id", business_id).eq("client_id", client_id)
             .in_("status", ["pending", "partial", "overdue"])
             .limit(1).execute())
        return bool(r.data)
    except Exception:
        return True


def _client_name(db, client_id: str) -> str:
    try:
        r = (db.table("clients").select("name").eq("id", client_id).limit(1).execute()).data
        return (r[0].get("name") if r else None) or "A customer"
    except Exception:
        return "A customer"


async def run() -> None:
    if not settings.enable_promise_capture:
        return
    db = require_db()
    due = promises.due_followups(db)
    if not due:
        return
    kept = broken = 0
    for p in due:
        biz_id, client_id = p.get("business_id"), p.get("client_id")
        pid = p.get("id")
        if not (biz_id and client_id and pid):
            continue
        try:
            if not _has_open_bills(db, biz_id, client_id):
                promises.mark(db, pid, "kept", followup=True)
                kept += 1
                continue
            # Still unpaid: release the hold and tell the owner.
            promises.mark(db, pid, "broken", followup=True)
            broken += 1
            name = _client_name(db, client_id)
            when = p.get("promise_date")
            said = f"promised {when}" if p.get("kind") == "promise" and when else "said they had paid"
            await whatsapp.notify_owner(
                biz_id,
                f"{name} {said}, and it is still unpaid. Reminders have resumed.")
        except Exception:
            log.exception("promise follow-up failed for %s - continuing", pid)
            continue
    if kept or broken:
        log.info("Promise follow-up: %d kept, %d broken (resumed)", kept, broken)
