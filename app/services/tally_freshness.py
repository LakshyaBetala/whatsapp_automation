"""How old are this shop's figures, and may we still chase on them?

A shop does not leave TallyPrime open all day. While it is shut, ASVA's picture
of who owes what stops moving, but the reminder cadence does not: a customer
who paid on Tuesday gets chased on Thursday for money they already sent. That
is worse than silence. It is the relationship damage the product exists to
prevent, and the owner hears about it from the customer, not from us.

This got sharper the moment WhatsApp moved onto ASVA's server. Before, a shut
Tally usually meant a shut laptop, which meant nothing went out - the two
failures masked each other. The server sends regardless.

So past `settings.tally_stale_hold_h` without a sync, reminders are held rather
than sent on figures nobody can stand behind. Bills are NOT held: a bill the
owner just made is new information, not a stale balance.

Two deliberate softenings, because a hold that fires wrongly is itself a
failure:
  * a shop whose stamp we cannot read is treated as FRESH. An unapplied
    migration, a missing column or a bad timestamp must never silently stop a
    whole shop's collections.
  * the owner can answer "keep chasing anyway" and that answer stands for
    `settings.tally_stale_ack_h`, because it is their ledger and their call.
"""
from __future__ import annotations

import datetime as _dt
import logging

from app.config import settings

log = logging.getLogger(__name__)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse(ts) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def hours_since_sync(biz: dict) -> float | None:
    """Hours since Tally data last arrived, or None when we cannot tell."""
    at = _parse(biz.get("last_tally_sync_at"))
    if at is None:
        return None
    return max(0.0, (_now() - at).total_seconds() / 3600.0)


def last_sync_label(biz: dict, *, english: bool = True) -> str:
    """Something an owner can act on: a weekday, not '73.4 hours'."""
    at = _parse(biz.get("last_tally_sync_at"))
    if at is None:
        return "a while ago" if english else "kaafi der pehle"
    try:
        local = at.astimezone(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
    except Exception:
        local = at
    days = (_now() - at).days
    if days == 0:
        return local.strftime("today at %I:%M %p").replace(" 0", " ")
    if days == 1:
        return local.strftime("yesterday at %I:%M %p").replace(" 0", " ")
    if days < 7:
        return local.strftime("%A")                       # "Tuesday"
    return local.strftime("%d %b")                        # "12 Oct"


def ack_active(biz: dict) -> bool:
    """The owner said to keep chasing on the old figures, recently enough."""
    at = _parse(biz.get("tally_stale_ack_at"))
    if at is None:
        return False
    return (_now() - at).total_seconds() / 3600.0 < settings.tally_stale_ack_h


def is_stale(biz: dict) -> bool:
    """Should reminders be held for this shop right now?

    False whenever we are unsure. A hold is a whole shop's collections stopped,
    so it only ever fires on a figure we actually read.
    """
    if not settings.tally_stale_hold_h:
        return False                                  # feature off
    hrs = hours_since_sync(biz)
    if hrs is None:
        return False                                  # unknown: never hold
    if hrs < settings.tally_stale_hold_h:
        return False
    return not ack_active(biz)


def stamp_sync(db, business_id: str) -> None:
    """Tally data arrived. Clears any standing 'chase anyway' answer, because
    the question it answered is gone."""
    try:
        db.table("businesses").update({
            "last_tally_sync_at": _now().isoformat(),
            "tally_stale_ack_at": None,
        }).eq("id", business_id).execute()
    except Exception:
        # Pre-migration, or a transient write failure. Never fail a sync over
        # bookkeeping: a missing stamp reads as "fresh", which is the safe way
        # round.
        log.debug("tally freshness stamp skipped for %s", business_id, exc_info=True)


def ack_stale(db, business_id: str) -> None:
    """Owner answered 'keep chasing on the old figures'."""
    try:
        db.table("businesses").update(
            {"tally_stale_ack_at": _now().isoformat()}).eq("id", business_id).execute()
    except Exception:
        log.warning("could not record the chase-anyway answer for %s",
                    business_id, exc_info=True)


def stale_ids(db, business_ids: list) -> set:
    """Which of these shops are too stale to chase on.

    Deliberately its OWN query rather than two more columns on the sweep's main
    select: until migration 060 is applied those columns do not exist, and a
    postgrest error on the sweep's central fetch would stop every reminder for
    every shop. A failure here returns an empty set, so the worst case is the
    behaviour we have today rather than a silent fleet-wide stop.
    """
    if not business_ids or not settings.tally_stale_hold_h:
        return set()
    try:
        rows = (db.table("businesses")
                .select("id, last_tally_sync_at, tally_stale_ack_at")
                .in_("id", list(business_ids)).execute()).data or []
    except Exception:
        log.info("tally freshness: columns unavailable (migration 060 not applied?) "
                 "- not holding anything", exc_info=True)
        return set()
    return {r["id"] for r in rows if is_stale(r)}
