"""Who to chase first: the ten parties the owner would have named himself.

A fleet audit on 2026-11-01 answered a question nobody had thought to ask. Of
the three shops whose owners actually opened ASVA, every single one had used it
to switch ASVA off: 915 of 1,043 parties at one shop, 270 of 272 at another,
187 of 197 at the third. The shops showing zero opt-outs were the ones that
synced once and never came back.

The default was the problem, not the reminders. ASVA arrived saying "I will now
chase all 1,043 of your customers", and no owner wants that on day one - these
are twenty-year relationships, not a lead list. So the default inverted: a
party arrives with reminders OFF, and the owner picks the handful that actually
annoy him. Ten is a number a shopkeeper can live with.

This module is that pick, and the proof that earns the next fifty.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

STARTER = 10          # the first ask: ten names, nothing more
NEXT_STEP = 50        # what we ask for once the ten have proved themselves
REVIEW_AFTER_DAYS = 14
REVIEW_UNTIL = 60     # above this the shop has committed; stop asking


def _today() -> _dt.date:
    return _dt.datetime.now(IST).date()


def rank(clients: list, totals: dict, overdue_days: dict) -> list:
    """Order parties by how annoying they actually are, worst first.

    Takes the two dicts the parties page has already built (client_id ->
    outstanding, client_id -> max days past due) so ranking costs no extra
    read. Money stuck times how long it has been stuck: a lakh three days late
    is business, twenty thousand a year late is the customer this is about.

    Only parties ASVA could actually chase are returned - a number to send to,
    not excluded by the owner, not already on, and real money outstanding.
    """
    out = []
    for c in clients:
        if c.get("reminders_enabled") or c.get("excluded"):
            continue
        if not (c.get("whatsapp_number") or "").strip():
            continue
        amount = Decimal(str(totals.get(c["id"], 0) or 0))
        if amount <= 0:
            continue
        late = int(overdue_days.get(c["id"], 0) or 0)
        out.append({
            "id": c["id"],
            "name": c.get("name") or "",
            "amount": amount,
            "days": late,
            # A shop where nothing is overdue yet still gets a sensible list:
            # late=0 collapses the score to the amount alone.
            "score": amount * (late or 1),
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def enable(db, business_id: str, client_ids: list) -> int:
    """Switch reminders on for exactly these parties, anchored today.

    The anchor is what dates the cadence, so a party picked today starts at the
    beginning of its sequence instead of jumping to whatever point its oldest
    bill has already passed. Scoped by business_id - there is no RLS.
    """
    ids = [str(i) for i in client_ids if i]
    if not ids:
        return 0
    today = _today().isoformat()
    done = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        db.table("clients").update(
            {"reminders_enabled": True, "reminder_anchor": today}
        ).eq("business_id", business_id).in_("id", chunk).execute()
        done += len(chunk)
    return done


def collected_for(db, business_id: str, client_ids: list, since: _dt.date) -> Decimal:
    """What these specific parties have paid since a date. Best effort -> 0."""
    ids = [str(i) for i in client_ids if i]
    if not ids:
        return Decimal(0)
    total = Decimal(0)
    try:
        for i in range(0, len(ids), 100):
            rows = (db.table("tally_receipts").select("amount")
                    .eq("business_id", business_id)
                    .in_("client_id", ids[i:i + 100])
                    .gte("receipt_date", since.isoformat()).execute()).data or []
            total += sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal(0))
    except Exception:
        return Decimal(0)
    return total


def picked_on(clients: list) -> _dt.date | None:
    """The day this shop first pointed ASVA at somebody."""
    days = []
    for c in clients:
        if not c.get("reminders_enabled"):
            continue
        raw = c.get("reminder_anchor")
        if not raw:
            continue
        try:
            days.append(_dt.date.fromisoformat(str(raw)[:10]))
        except ValueError:
            pass
    return min(days) if days else None


def stage(clients: list, today: _dt.date | None = None) -> dict:
    """Where this shop is in "start with ten, earn the rest".

    Returns which of three things the owner should be shown:
      pick    - nobody is being chased yet, ask for ten
      review  - the ten have had two weeks, show what they paid and ask for fifty
      none    - mid-trial, or the shop has already committed past REVIEW_UNTIL
    """
    today = today or _today()
    on = [c for c in clients if c.get("reminders_enabled") and not c.get("excluded")]
    if not on:
        return {"what": "pick", "on": 0, "since": None, "ids": []}
    if len(on) >= REVIEW_UNTIL:
        return {"what": "none", "on": len(on), "since": None, "ids": []}
    since = picked_on(clients)
    if since and (today - since).days >= REVIEW_AFTER_DAYS:
        return {"what": "review", "on": len(on), "since": since,
                "ids": [c["id"] for c in on]}
    return {"what": "none", "on": len(on), "since": since, "ids": []}
