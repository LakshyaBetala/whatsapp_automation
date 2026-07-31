"""Morning pre-reminder checkpoint state (Option A: HOLD + nudge, never mark paid).

Before ASVA reminds anyone, it previews today's list to the owner. The owner can
HOLD parties that already paid; those reminders are skipped today and the owner
is nudged to enter the receipt in Tally. Nothing is written to Tally and no bill
is marked paid here - Tally stays the source of truth, and a held party comes
back on its next cadence day until the receipt appears.

State lives on the business row (checkpoint_date / _items / _held), mirroring the
catchup_* pattern. Every read/write TOLERATES the columns being absent (migration
027 not yet applied), so the sweep and bot never break on an un-migrated DB - the
feature simply stays off until the migration lands.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger(__name__)
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _today() -> str:
    return _dt.datetime.now(IST).date().isoformat()


def set_today(db, business_id: str, items: list[dict]) -> bool:
    """Store today's previewed list (ordered) and clear holds. Storing even an
    empty list marks 'previewed today' so we don't re-message the owner."""
    try:
        db.table("businesses").update({
            "checkpoint_date": _today(),
            "checkpoint_items": items,
            "checkpoint_held": [],
        }).eq("id", business_id).execute()
        return True
    except Exception:
        log.warning("checkpoint set_today failed (apply migration 027?) - continuing")
        return False


def get_today(db, business_id: str) -> dict | None:
    """{items, held} for today, or None if there is no active checkpoint (or the
    columns are missing)."""
    try:
        r = (db.table("businesses")
             .select("checkpoint_date, checkpoint_items, checkpoint_held")
             .eq("id", business_id).limit(1).execute()).data
    except Exception:
        return None
    if not r or str(r[0].get("checkpoint_date") or "")[:10] != _today():
        return None
    return {"items": r[0].get("checkpoint_items") or [],
            "held": list(r[0].get("checkpoint_held") or [])}


def hold(db, business_id: str, client_id: str) -> bool:
    """Add one party to today's hold list (skip its reminder today)."""
    cur = get_today(db, business_id)
    if cur is None:
        return False
    held = sorted(set(cur["held"]) | {client_id})
    try:
        db.table("businesses").update({"checkpoint_held": held}).eq("id", business_id).execute()
        return True
    except Exception:
        return False


def hold_all(db, business_id: str) -> int:
    """Hold every party in today's list. Returns how many."""
    cur = get_today(db, business_id)
    if cur is None:
        return 0
    ids = [it["id"] for it in cur["items"] if it.get("id")]
    try:
        db.table("businesses").update({"checkpoint_held": ids}).eq("id", business_id).execute()
        return len(ids)
    except Exception:
        return 0


def held_sets(db, business_ids: list[str]) -> dict[str, set]:
    """{business_id: set(held client_ids)} for businesses whose checkpoint is for
    TODAY. One query, tolerant of missing columns (returns {} then). The sweep
    calls this once and skips any party the owner held this morning."""
    if not business_ids:
        return {}
    today = _today()
    try:
        rows = (db.table("businesses")
                .select("id, checkpoint_date, checkpoint_held")
                .in_("id", business_ids).execute()).data or []
    except Exception:
        return {}
    out: dict[str, set] = {}
    for r in rows:
        if str(r.get("checkpoint_date") or "")[:10] == today:
            out[r["id"]] = set(r.get("checkpoint_held") or [])
    return out
