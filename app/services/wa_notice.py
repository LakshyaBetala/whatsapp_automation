"""Tell the owner when their WhatsApp has stopped sending, once, not every cycle.

A linked WhatsApp drops silently: the phone logs the device out, the phone stays
off for days, WhatsApp gets reinstalled. Nothing looks broken from the shop's
side. ASVA keeps queueing bills and reminders, the owner assumes they went out,
and the shop quietly stops chasing anyone for days. That is the one thing worth
interrupting an owner for.

The notice goes from the ASVA bot number, because the shop's own WhatsApp is the
thing that is down. Four rules keep it help instead of nagging:
  - only once the disconnection has lasted GRACE_MIN (a reconnect blip during a
    phone reboot is not an incident, and must never earn a message),
  - only inside the send window (nobody can scan a QR at 2am),
  - at most one notice a day while it stays down,
  - one "sending again" line when it comes back, and only to an owner we
    actually interrupted.

Detection is not repeated here: monitoring.evaluate already decides what counts
as a disconnected shop (and ignores shops whose laptop is merely off, where a
stale flag means nothing). This module only decides who to tell, and when.
"""
from __future__ import annotations

import datetime as _dt
import logging

from app.jobs.outbox_sweep import within_send_window
from app.services import i18n, whatsapp

log = logging.getLogger(__name__)

GRACE_MIN = 20          # how long it must stay down before the owner hears about it
REPEAT_HOURS = 24       # while still down: one reminder a day, no more
RECENT_HOURS = 72       # "sending again" only closes a notice from the last 3 days

DOWN = "wa_down_notice"
BACK = "wa_back_notice"
STALE = "tally_stale_notice"
FRESH = "tally_fresh_notice"

_DOWN_EN = (
    "{name}: your WhatsApp is not connected to ASVA right now, so bills and "
    "payment reminders are not going out.{waiting}\n\n"
    "To start them again, on your Tally computer:\n"
    "1. Open ASVA\n"
    "2. Go to WhatsApp\n"
    "3. Scan the QR with this phone\n\n"
    "Nothing is lost. Everything that is waiting goes out as soon as you are back."
)
_DOWN_HI = (
    "{name}: aapka WhatsApp abhi ASVA se connected nahi hai, is liye bills aur "
    "payment reminders ja nahi rahe hain.{waiting}\n\n"
    "Phir se shuru karne ke liye, Tally wale computer par:\n"
    "1. ASVA kholiye\n"
    "2. WhatsApp par jaaiye\n"
    "3. Is phone se QR scan kijiye\n\n"
    "Kuch bhi nahi gaya hai. Jo ruka hai woh aapke connect hote hi chala jaayega."
)
_BACK_EN = ("{name}: your WhatsApp is connected again. Bills and reminders are "
            "going out from your number as usual.")

# Stale figures. The owner has to be able to act on this without opening a
# dashboard, so it says exactly what stopped, exactly what to do, and offers
# the one answer only they can give.
_STALE_EN = (
    "{name}: ASVA last read your Tally {when}, so the outstanding figures are "
    "now {days} days old.\n\n"
    "I have PAUSED payment reminders. Chasing someone on {days}-day-old figures "
    "risks asking a customer for money they have already paid.\n\n"
    "To get the latest figures:\n"
    "1. Open TallyPrime on the shop computer\n"
    "2. Open ASVA and press Refresh\n"
    "Reminders restart on their own within a minute. Nothing is lost.\n\n"
    "If you would rather keep chasing on {when}'s figures for now, reply CHASE."
)
_STALE_HI = (
    "{name}: ASVA ne aapki Tally {when} padhi thi, is liye outstanding ke "
    "figures ab {days} din purane hain.\n\n"
    "Maine payment reminders ROK diye hain. {days} din purane figures par chase "
    "karne se aisa customer bhi maang baithega jisne paisa de diya hai.\n\n"
    "Nayi figures ke liye:\n"
    "1. Shop computer par TallyPrime kholiye\n"
    "2. ASVA kholkar Refresh dabaiye\n"
    "Reminders ek minute me apne aap chalu ho jayenge. Kuch nahi gaya.\n\n"
    "Agar abhi {when} wale figures par hi chase karna hai to CHASE reply karein."
)
_FRESH_EN = ("{name}: Tally is synced again and the figures are current. "
             "Payment reminders have restarted.")
_FRESH_HI = ("{name}: Tally phir se sync ho gayi hai aur figures aaj ke hain. "
             "Payment reminders chalu ho gaye hain.")
_BACK_HI = ("{name}: aapka WhatsApp phir se connected hai. Bills aur reminders "
            "aapke number se jaise pehle ja rahe hain.")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse(ts) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _mins(since: _dt.datetime | None) -> float:
    return 10 ** 9 if since is None else (_now() - since).total_seconds() / 60.0


def _last_notice(db, business_id: str, template: str) -> _dt.datetime | None:
    """When this shop last got this notice (any delivery outcome: a notice we
    tried and failed to send must not be retried every five minutes)."""
    try:
        r = (db.table("messages").select("created_at")
             .eq("business_id", business_id).eq("template_name", template)
             .order("created_at", desc=True).limit(1).execute())
    except Exception:
        log.warning("wa_notice: could not read last %s", template, exc_info=True)
        return _now()                      # unknown: stay quiet rather than risk a repeat
    return _parse(r.data[0]["created_at"]) if r.data else None


def _down_since(db, business_id: str) -> _dt.datetime | None:
    """When the open wa_down incident for this shop was raised."""
    try:
        r = (db.table("alert_log").select("created_at").eq("kind", "wa_down")
             .eq("business_id", business_id).is_("resolved_at", "null")
             .order("created_at", desc=True).limit(1).execute())
    except Exception:
        return None
    return _parse(r.data[0]["created_at"]) if r.data else None


async def _send(db, biz: dict, template: str, text: str) -> bool:
    number = (biz.get("whatsapp_number") or "").strip()
    if not number:
        return False
    try:
        # notify_owner goes out on the platform (bot) number, which is the whole
        # point: the shop's own WhatsApp is the thing that is down.
        await whatsapp.notify_owner(biz["id"], text, template_name=template)
        return True
    except Exception:
        log.warning("wa_notice: %s send failed for %s", template, biz.get("id"), exc_info=True)
        return False


async def run(db, problems: list[dict], health: dict) -> int:
    """Notify owners whose WhatsApp is down, and those whose came back.

    `problems` and `health` are the watchdog's own snapshot, so this adds no
    extra scanning. Returns how many messages were sent.
    """
    if not within_send_window():
        return 0

    down_ids = {p["business_id"] for p in problems
                if p.get("kind") == "wa_down" and p.get("business_id")}
    rows = {r["id"]: r for r in health.get("businesses", [])}
    if not rows:
        return 0

    bizes = {}
    try:
        r = (db.table("businesses")
             .select("id, business_name, whatsapp_number, msg_language, owner_language")
             .in_("id", list(rows)).execute())
        bizes = {b["id"]: b for b in (r.data or [])}
    except Exception:
        log.warning("wa_notice: could not load shops", exc_info=True)
        return 0

    # Shops whose figures are too old to chase on. Own query, because the
    # columns may predate migration 060 - see tally_freshness.stale_ids.
    from app.services import tally_freshness as fresh
    stale_ids = fresh.stale_ids(db, list(rows))

    sent = 0
    for bid, row in rows.items():
        biz = bizes.get(bid)
        if not biz:
            continue
        name = biz.get("business_name") or "Your shop"
        english = i18n.is_english(biz.get("owner_language") or biz.get("msg_language"))

        if bid in down_ids:
            if _mins(_down_since(db, bid)) < GRACE_MIN:
                continue                   # a blip, not an outage
            last = _last_notice(db, bid, DOWN)
            if last is not None and _mins(last) < REPEAT_HOURS * 60:
                continue                   # already told today
            queued = int(row.get("queued") or 0)
            waiting = (f" {queued} message{'s' if queued != 1 else ''} "
                       f"{'are' if queued != 1 else 'is'} waiting." if queued else "")
            if not english and queued:
                waiting = f" {queued} message ruke hue hain."
            text = (_DOWN_EN if english else _DOWN_HI).format(name=name, waiting=waiting)
            if await _send(db, biz, DOWN, text):
                sent += 1
                log.info("wa_notice: told %s their WhatsApp is disconnected", name)
            continue

        # Figures too old to chase on: say what stopped and how to restart it.
        if bid in stale_ids:
            last = _last_notice(db, bid, STALE)
            if last is not None and _mins(last) < REPEAT_HOURS * 60:
                continue                   # already told today
            full = bizes.get(bid) or {}
            when = fresh.last_sync_label(full, english=english)
            hrs = fresh.hours_since_sync(full)
            text = (_STALE_EN if english else _STALE_HI).format(
                name=name, when=when, days=int((hrs or 0) // 24) or 1)
            if await _send(db, biz, STALE, text):
                sent += 1
                log.info("wa_notice: told %s their Tally figures are stale", name)
            continue

        # Synced again, and we had interrupted them about it: close that loop.
        last_stale = _last_notice(db, bid, STALE)
        if (last_stale is not None and _mins(last_stale) <= RECENT_HOURS * 60):
            last_fresh = _last_notice(db, bid, FRESH)
            if last_fresh is None or last_fresh < last_stale:
                if await _send(db, biz, FRESH,
                               (_FRESH_EN if english else _FRESH_HI).format(name=name)):
                    sent += 1

        # Back up: close the loop, but only with an owner we interrupted.
        if row.get("wa_ready") is not True:
            continue
        last_down = _last_notice(db, bid, DOWN)
        if last_down is None or _mins(last_down) > RECENT_HOURS * 60:
            continue
        last_back = _last_notice(db, bid, BACK)
        if last_back is not None and last_back > last_down:
            continue
        if await _send(db, biz, BACK, (_BACK_EN if english else _BACK_HI).format(name=name)):
            sent += 1
    return sent
