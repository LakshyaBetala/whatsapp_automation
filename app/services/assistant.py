"""The ASVA assistant's brain for WHEN to speak on the marketing/bot number.

Two jobs, both about not being annoying:

  1. A founder-level global switch (platform_config.assistant_enabled). When OFF,
     the bot stops auto-replying to prospects entirely - the founder is handling
     leads by hand. Owner commands (LIST/BILL/...) are unaffected; this only
     governs the prospect auto-pitch.

  2. Per-lead funnel state (leads table) so the bot pitches ONCE and then goes
     quiet, and hands over to a human the moment a lead says YES - staying silent
     for a window so it never talks over a real sales conversation.

Everything is best-effort and tolerant of the tables being absent (migration 046
not yet applied): a missing table degrades to "assistant on, pitch once", never
an exception. No message content is ever stored - only status + timestamps.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger(__name__)

HANDOVER_HOURS = 12
_SWITCH_KEY = "assistant_enabled"

# Decisions the funnel can return.
PITCH = "pitch"        # send the one-time pitch
HANDOVER = "handover"  # lead said yes -> warm hand-off, then go quiet
SILENT = "silent"      # already pitched / human handling -> say nothing


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ── Global On/Off switch ──────────────────────────────────────────────────
def assistant_enabled(db) -> bool:
    """Is the marketing/bot auto-reply ON? Defaults to True (missing row/table)."""
    try:
        r = (db.table("platform_config").select("value")
             .eq("key", _SWITCH_KEY).limit(1).execute()).data
        if not r:
            return True
        return str(r[0].get("value")).lower() != "false"
    except Exception:
        return True


def set_assistant_enabled(db, on: bool) -> bool:
    """Founder flips the global switch. Returns the new state; best-effort."""
    try:
        db.table("platform_config").upsert(
            {"key": _SWITCH_KEY, "value": "true" if on else "false",
             "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception:
        log.warning("assistant switch write failed (apply migration 046?)")
    return on


# ── Per-lead funnel ───────────────────────────────────────────────────────
def _get_lead(db, from_number: str) -> dict | None:
    try:
        r = (db.table("leads").select("*")
             .eq("from_number", from_number).limit(1).execute()).data
        return r[0] if r else None
    except Exception:
        return None


def _handover_active(lead: dict | None) -> bool:
    if not lead or not lead.get("handover_until"):
        return False
    try:
        until = _dt.datetime.fromisoformat(str(lead["handover_until"]).replace("Z", "+00:00"))
        return until > _now()
    except (TypeError, ValueError):
        return False


def decide_prospect(db, from_number: str, is_yes: bool) -> str:
    """The smart decision for a message from a non-owner on the bot number:
      - a live hand-over window  -> SILENT (a human is on it)
      - the lead says YES        -> HANDOVER (warm hand-off + start the window)
      - not pitched yet          -> PITCH (the one and only pitch)
      - already pitched          -> SILENT (never re-pitch / talk over them)
    Records only status + timestamps; never message content.
    """
    lead = _get_lead(db, from_number)

    if _handover_active(lead):
        _bump(db, from_number, lead, status="handover")
        return SILENT

    if is_yes:
        _set_handover(db, from_number, lead)
        return HANDOVER

    already_pitched = bool(lead and lead.get("pitched_at"))
    if not already_pitched:
        _mark_pitched(db, from_number, lead)
        return PITCH

    _bump(db, from_number, lead, status=lead.get("status") or "pitched")
    return SILENT


def clear_handover(db, from_number: str) -> None:
    """Turn the assistant back ON for one lead (the founder is done handling it)."""
    try:
        db.table("leads").update(
            {"handover_until": None, "status": "pitched", "updated_at": _now().isoformat()}
        ).eq("from_number", from_number).execute()
    except Exception:
        pass


# ── writes (best-effort) ──────────────────────────────────────────────────
def _mark_pitched(db, from_number: str, lead: dict | None) -> None:
    row = {"from_number": from_number, "status": "pitched",
           "pitched_at": _now().isoformat(),
           "msg_count": (lead.get("msg_count", 0) if lead else 0) + 1,
           "updated_at": _now().isoformat()}
    try:
        db.table("leads").upsert(row, on_conflict="from_number").execute()
    except Exception:
        pass


def _set_handover(db, from_number: str, lead: dict | None) -> None:
    until = (_now() + _dt.timedelta(hours=HANDOVER_HOURS)).isoformat()
    row = {"from_number": from_number, "status": "handover",
           "handover_until": until,
           "pitched_at": (lead.get("pitched_at") if lead else None) or _now().isoformat(),
           "msg_count": (lead.get("msg_count", 0) if lead else 0) + 1,
           "updated_at": _now().isoformat()}
    try:
        db.table("leads").upsert(row, on_conflict="from_number").execute()
    except Exception:
        pass


def _bump(db, from_number: str, lead: dict | None, status: str) -> None:
    try:
        db.table("leads").upsert(
            {"from_number": from_number, "status": status,
             "msg_count": (lead.get("msg_count", 0) if lead else 0) + 1,
             "updated_at": _now().isoformat()}, on_conflict="from_number").execute()
    except Exception:
        pass


def recent_leads(db, limit: int = 25) -> list[dict]:
    """For the Command Center: recent leads + their state (no message content)."""
    try:
        return (db.table("leads").select("from_number, status, pitched_at, handover_until, msg_count, updated_at")
                .order("updated_at", desc=True).limit(limit).execute()).data or []
    except Exception:
        return []
