"""Cash-in forecast: money likely to arrive in the next few days.

Turns ASVA from a reminder tool into a cash-flow tool. It answers the one
question every distributor asks on a tight week: "how much will actually come
in?" Two honest buckets, no guessing:

  - promised  : parties who said they paid, or promised a date within the
                horizon (high confidence). Amount = what they named, else their
                open outstanding.
  - due_soon  : open bills whose due date falls inside the horizon and whose
                party has NOT already promised (so nothing is counted twice).

Pure, best-effort reads (one bills fetch + the open-promise list), so the bot,
the digest, and the dashboard can all quote the same number.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _today() -> _dt.date:
    return _dt.datetime.now(IST).date()


def _money(x) -> Decimal:
    try:
        return Decimal(str(x if x is not None else 0))
    except Exception:
        return Decimal(0)


def cash_in_forecast(db, business_id: str, *, horizon_days: int = 7,
                     today: _dt.date | None = None) -> dict:
    """What is likely to land in the next ``horizon_days``. Returns
    {horizon_days, promised, due_soon, total, promised_count, due_count}.
    Best-effort: any error yields zeros rather than raising."""
    today = today or _today()
    horizon = today + _dt.timedelta(days=max(1, horizon_days))

    try:
        bills = (db.table("bills").select("client_id, outstanding, due_date")
                 .eq("business_id", business_id)
                 .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    except Exception:
        log.debug("forecast: bills fetch failed (%s)", business_id, exc_info=True)
        bills = []

    out_by_client: dict = {}
    due_soon_by_client: dict = {}
    for b in bills:
        cid = b.get("client_id")
        amt = _money(b.get("outstanding"))
        if not cid or amt <= 0:
            continue
        out_by_client[cid] = out_by_client.get(cid, Decimal(0)) + amt
        dd = b.get("due_date")
        if dd:
            try:
                d = _dt.date.fromisoformat(str(dd)[:10])
            except (TypeError, ValueError):
                continue
            if today <= d <= horizon:
                due_soon_by_client[cid] = due_soon_by_client.get(cid, Decimal(0)) + amt

    try:
        from app.services import promises as pr
        open_promises = pr.open_for_business(db, business_id)
    except Exception:
        open_promises = []

    promised_by_client: dict = {}
    for p in open_promises:
        cid = p.get("client_id")
        if not cid:
            continue
        kind = p.get("kind")
        if kind == "paid_claim":
            include = True                       # already claims paid -> imminent
        else:                                    # 'promise' -> only if within horizon
            pd = p.get("promise_date")
            try:
                include = (not pd) or _dt.date.fromisoformat(str(pd)[:10]) <= horizon
            except (TypeError, ValueError):
                include = True                   # unparseable date -> count it, don't drop
        if not include:
            continue
        # Ground every promise in the party's REAL open balance. A parsed amount
        # is trusted only when it is positive AND does not exceed what they owe;
        # a promise larger than the debt (or a misread number from free text) is
        # almost certainly a mis-parse, so we fall back to the true outstanding.
        # This stops the "coming in" figure from showing random inflated amounts.
        owed = out_by_client.get(cid, Decimal(0))
        if owed <= 0:
            continue                              # nothing owed -> nothing coming
        raw = _money(p.get("amount")) if p.get("amount") else Decimal(0)
        amt = raw if (Decimal(0) < raw <= owed) else owed
        if amt > 0:
            # one figure per party (the largest signal), never stacked
            promised_by_client[cid] = max(promised_by_client.get(cid, Decimal(0)), amt)

    promised = sum(promised_by_client.values(), Decimal(0))
    # due_soon excludes parties already in 'promised' so nothing is double-counted
    due_soon = sum((v for cid, v in due_soon_by_client.items()
                    if cid not in promised_by_client), Decimal(0))
    due_count = len([c for c in due_soon_by_client if c not in promised_by_client])

    return {
        "horizon_days": horizon_days,
        "promised": promised,
        "due_soon": due_soon,
        "total": promised + due_soon,
        "promised_count": len(promised_by_client),
        "due_count": due_count,
    }


def forecast_line(f: dict, *, en: bool = True) -> str:
    """One clean line for the digest / bot: the headline number + how it splits.
    Returns '' when there is nothing expected (so callers can skip it)."""
    from app.services.templates import inr
    total = f.get("total") or 0
    if total <= 0:
        return ""
    promised = f.get("promised") or 0
    due = f.get("due_soon") or 0
    days = f.get("horizon_days", 7)
    parts = []
    if promised > 0:
        parts.append((f"{inr(promised)} promised" if en else f"{inr(promised)} promised"))
    if due > 0:
        parts.append((f"{inr(due)} due" if en else f"{inr(due)} due"))
    split = (" (" + " + ".join(parts) + ")") if parts else ""
    if en:
        return f"COMING IN (next {days} days): about {inr(total)}{split}"
    return f"AGLE {days} DIN MEIN AANE WALA: lagbhag {inr(total)}{split}"
