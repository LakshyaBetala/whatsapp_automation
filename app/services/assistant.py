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


# ── Onboarding nudges: welcome a shop once it syncs, chase one that hasn't ──────
# Both message the OWNER on the bot number (never a customer), are sent at most
# once per shop (dedup stamps from migration 048), and degrade silently if that
# migration is not applied.

def _owner_is_english(biz: dict) -> bool:
    v = (biz.get("owner_language") or biz.get("msg_language") or "").strip().lower()
    return v not in ("hinglish", "hindi", "hi")


_WELCOME_EN = (
    "Namaste. ASVA is all set up and reading your Tally. I have loaded your "
    "customers and what each one owes you.\n\n"
    "From now on I remind them on WhatsApp from your own number, and every night I "
    "send you one summary: who paid and who to chase.\n\n"
    "Reply *LIST* to see who owes you right now, or *DIGEST* for tonight's summary. "
    "I am here whenever you need me.")
_WELCOME_HI = (
    "Namaste. ASVA poori tarah set ho gaya hai aur aapki Tally padh raha hai. "
    "Aapke customers aur unka baaki amount load ho gaya hai.\n\n"
    "Ab main aapke apne number se unhe WhatsApp par reminder bhejta rahunga, aur roz "
    "raat ek summary dunga: kisne pay kiya aur kise chase karna hai.\n\n"
    "Abhi kaun kitna dena hai dekhne ke liye *LIST* likhein, ya aaj ki summary ke "
    "liye *DIGEST*. Zaroorat ho toh yahin batayein.")

_NUDGE_EN = (
    "Namaste. Your ASVA is ready but not activated yet.\n\n"
    "Please open ASVA on the computer where TallyPrime runs and press *Refresh* "
    "once. That loads your customers and outstanding, and your setup is done.\n\n"
    "Do this soon so your setup code stays valid. Reply here if you need any help, "
    "we will guide you.")
_NUDGE_HI = (
    "Namaste. Aapka ASVA taiyaar hai lekin abhi activate nahi hua.\n\n"
    "Kripya jis computer par TallyPrime chalta hai wahan ASVA kholein aur ek baar "
    "*Refresh* dabayein. Isse aapke customers aur baaki amount load ho jayenge aur "
    "setup poora ho jayega.\n\n"
    "Yeh jaldi kar lein taaki aapka setup code valid rahe. Madad chahiye toh yahin "
    "reply karein, hum guide kar denge.")


async def welcome_owner_if_new(db, business_id: str) -> bool:
    """Send the one-time 'you're all set' welcome the first time a shop's Tally
    data syncs. Dedup on businesses.welcomed_at (stamped BEFORE the send so a retry
    can never double-welcome). Best-effort; never raises into the caller."""
    try:
        r = (db.table("businesses")
             .select("id, whatsapp_number, welcomed_at, owner_language, msg_language")
             .eq("id", str(business_id)).limit(1).execute()).data
        if not r:
            return False
        biz = r[0]
        if biz.get("welcomed_at") or not biz.get("whatsapp_number"):
            return False
        try:
            db.table("businesses").update({"welcomed_at": _now().isoformat()}) \
                .eq("id", str(business_id)).execute()
        except Exception:
            return False                      # column missing (048 not applied)
        from app.services import whatsapp
        await whatsapp.notify_owner(str(business_id),
                                    _WELCOME_EN if _owner_is_english(biz) else _WELCOME_HI)
        log.info("Sent first-sync welcome to business %s", business_id)
        return True
    except Exception:
        log.warning("welcome_owner_if_new failed", exc_info=True)
        return False


async def nudge_unsynced(db) -> int:
    """Nudge shops paired in the last week (agent token + owner WhatsApp) that have
    still loaded NO Tally data, so they open ASVA + Refresh before the setup code
    lapses. Once per shop (unsynced_nudge_at). The 7-day floor keeps this off old
    stale/test rows; returns how many were nudged."""
    try:
        now = _now()
        floor = (now - _dt.timedelta(days=7)).isoformat()      # only recent signups
        ceil = (now - _dt.timedelta(hours=3)).isoformat()      # give them a few hours first
        rows = (db.table("businesses")
                .select("id, whatsapp_number, owner_language, msg_language")
                .not_.is_("agent_token", "null")
                .not_.is_("whatsapp_number", "null")
                .is_("unsynced_nudge_at", "null")
                .gt("created_at", floor).lt("created_at", ceil)
                .limit(200).execute()).data or []
    except Exception:
        return 0
    from app.services import whatsapp
    sent = 0
    for biz in rows:
        bid = biz.get("id")
        if not bid:
            continue
        try:
            c = (db.table("clients").select("id", count="exact")
                 .eq("business_id", bid).limit(1).execute())
            if c.data:                        # already has data -> synced, skip
                continue
        except Exception:
            continue
        try:
            db.table("businesses").update({"unsynced_nudge_at": now.isoformat()}) \
                .eq("id", bid).execute()
        except Exception:
            return sent                       # column missing (048 not applied) -> stop
        try:
            await whatsapp.notify_owner(bid, _NUDGE_EN if _owner_is_english(biz) else _NUDGE_HI)
            sent += 1
        except Exception:
            log.warning("unsynced nudge send failed for %s", bid, exc_info=True)
    if sent:
        log.info("onboarding nudge: nudged %d unsynced shop(s)", sent)
    return sent
