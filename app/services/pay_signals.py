"""Expected payments: what the pay page tells us, and how it gets resolved.

A tap on "Pay by UPI" is the earliest signal a shop gets that money may be on
its way: who, how much, for which bill, when. It is INTENT, never a payment
(a UPI deep link hands the customer to their own app and nothing calls back),
so every surface says "tapped Pay" and nothing is ever written to Tally from
here. What this module does is keep each tap OPEN until the truth arrives, from
whichever of these gets there first:

    tally       a receipt for the party of about that amount lands via sync
    screenshot  the customer sends a payment screenshot from their number
    owner       the owner presses "Received, post to Tally" on the Payments tab
    dismissed   the owner presses "Not received"
    expired     nothing for EXPIRE_DAYS - silence is information too
    superseded  the customer tapped again with a different amount

Open taps are the "Expected" list on the Payments tab; resolved ones are the
party's history. All writes are best-effort: a signal must never break a sync,
a WhatsApp inbound, or a receipt post.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

log = logging.getLogger(__name__)

EXPIRE_DAYS = 3
# A receipt "matches" a tap when it is within this fraction of the amount the
# customer chose, or covers it. Bank charges and rounding live inside 1%.
MATCH_TOLERANCE = 0.01


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def record(db, business_id: str, client_id: Optional[str], kind: str,
           amount, note: str = "") -> None:
    """Log one pay-page event. A fresh tap supersedes an older open tap by the
    same customer (they changed the amount and pressed again), so the Expected
    list never shows two rows for one person."""
    if db is None:
        return
    try:
        if kind == "tapped" and client_id:
            (db.table("pay_link_events")
             .update({"resolved_at": _now().isoformat(), "resolved_by": "superseded"})
             .eq("business_id", business_id).eq("client_id", client_id)
             .eq("kind", "tapped").is_("resolved_at", "null").execute())
        row = {"business_id": business_id, "client_id": client_id, "kind": kind,
               "amount": float(amount or 0)}
        if note:
            row["note"] = str(note)[:60]
        db.table("pay_link_events").insert(row).execute()
    except Exception:
        log.debug("pay event %s not recorded", kind, exc_info=True)


def open_taps(db, business_id: str, days: int = EXPIRE_DAYS) -> list[dict]:
    """Unresolved taps in the last `days`, newest first, one per customer."""
    since = (_now() - _dt.timedelta(days=days)).isoformat()
    try:
        rows = (db.table("pay_link_events")
                .select("id, client_id, amount, note, created_at")
                .eq("business_id", business_id).eq("kind", "tapped")
                .is_("resolved_at", "null").gte("created_at", since)
                .order("created_at", desc=True).limit(50).execute()).data or []
    except Exception:
        log.debug("open taps unavailable", exc_info=True)
        return []
    seen, out = set(), []
    for r in rows:
        cid = r.get("client_id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(r)
    return out


def recently_resolved(db, business_id: str, hours: int = 24) -> list[dict]:
    """Taps closed in the last `hours` by evidence (tally / screenshot / owner),
    so the Payments tab can show a quiet tick before the row disappears."""
    since = (_now() - _dt.timedelta(hours=hours)).isoformat()
    try:
        return (db.table("pay_link_events")
                .select("id, client_id, amount, note, created_at, resolved_at, resolved_by")
                .eq("business_id", business_id).eq("kind", "tapped")
                .in_("resolved_by", ["tally", "screenshot", "owner"])
                .gte("resolved_at", since)
                .order("resolved_at", desc=True).limit(20).execute()).data or []
    except Exception:
        return []


def resolve(db, business_id: str, event_id: str, by: str) -> bool:
    try:
        r = (db.table("pay_link_events")
             .update({"resolved_at": _now().isoformat(), "resolved_by": by})
             .eq("business_id", business_id).eq("id", event_id)
             .is_("resolved_at", "null").execute())
        return bool(r.data)
    except Exception:
        log.debug("resolve %s failed", event_id, exc_info=True)
        return False


def resolve_for_receipt(db, business_id: str, client_id: Optional[str],
                        amount, by: str) -> int:
    """Money (or evidence of it) arrived for this party: close the open taps it
    accounts for. A receipt closes a tap when it is within MATCH_TOLERANCE of
    the tapped amount or covers it. Returns how many taps were closed."""
    if db is None or not client_id:
        return 0
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return 0
    if amt <= 0:
        return 0
    closed = 0
    for t in open_taps(db, business_id, days=EXPIRE_DAYS + 4):
        if t.get("client_id") != client_id:
            continue
        want = float(t.get("amount") or 0)
        if amt >= want * (1 - MATCH_TOLERANCE) and resolve(db, business_id, t["id"], by):
            closed += 1
    return closed


def resolve_for_client(db, business_id: str, client_id: Optional[str], by: str) -> int:
    """The owner acted on this party (booked a payment, or said "not received"):
    close every open tap for them, whatever the amount."""
    if db is None or not client_id:
        return 0
    closed = 0
    for t in open_taps(db, business_id, days=EXPIRE_DAYS + 4):
        if t.get("client_id") == client_id and resolve(db, business_id, t["id"], by):
            closed += 1
    return closed


def expire(db) -> int:
    """Daily: taps older than EXPIRE_DAYS with nothing seen become history."""
    if db is None:
        return 0
    cutoff = (_now() - _dt.timedelta(days=EXPIRE_DAYS)).isoformat()
    try:
        r = (db.table("pay_link_events")
             .update({"resolved_at": _now().isoformat(), "resolved_by": "expired"})
             .eq("kind", "tapped").is_("resolved_at", "null")
             .lt("created_at", cutoff).execute())
        n = len(r.data or [])
        if n:
            log.info("pay signals: %d tap(s) expired unresolved", n)
        return n
    except Exception:
        log.debug("pay signal expiry failed", exc_info=True)
        return 0


def history_for_client(db, business_id: str, client_id: str, limit: int = 6) -> list[dict]:
    """The party's pay-page timeline, newest first: opened, tapped, resolved."""
    try:
        return (db.table("pay_link_events")
                .select("kind, amount, note, created_at, resolved_at, resolved_by")
                .eq("business_id", business_id).eq("client_id", client_id)
                .order("created_at", desc=True).limit(limit).execute()).data or []
    except Exception:
        return []
