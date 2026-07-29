"""Proof of value: what ASVA recovered, and what is still out.

The number that renews a subscription and fuels referrals: "ASVA got back Rs X
for you this month." Recovered = receipts booked in the period (tally_receipts);
the rest is the live open outstanding. Pure, best-effort reads, so one function
can front the bot reply, the nightly digest, the mobile app, and the Command
Center - every surface quotes the same truth.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _today() -> _dt.date:
    return _dt.datetime.now(IST).date()


def _month_start(d: _dt.date) -> _dt.date:
    return d.replace(day=1)


def _next_month(first: _dt.date) -> _dt.date:
    return (first.replace(year=first.year + 1, month=1) if first.month == 12
            else first.replace(month=first.month + 1))


def _prev_month(first: _dt.date) -> _dt.date:
    return (first.replace(year=first.year - 1, month=12) if first.month == 1
            else first.replace(month=first.month - 1))


def recovered_between(db, business_id: str, start: _dt.date, end: _dt.date) -> Decimal:
    """Sum of receipts booked in [start, end). Best-effort -> 0 on any error."""
    try:
        rows = (db.table("tally_receipts").select("amount")
                .eq("business_id", business_id)
                .gte("receipt_date", start.isoformat())
                .lt("receipt_date", end.isoformat()).execute()).data or []
        return sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal(0))
    except Exception:
        return Decimal(0)


def outstanding_total(db, business_id: str) -> Decimal:
    """Sum of what is still open (pending/partial/overdue). Best-effort -> 0."""
    try:
        rows = (db.table("bills").select("outstanding")
                .eq("business_id", business_id)
                .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
        return sum((Decimal(str(r.get("outstanding") or 0)) for r in rows), Decimal(0))
    except Exception:
        return Decimal(0)


def build_proof(db, business_id: str, today: _dt.date | None = None) -> dict:
    """The value snapshot for one business: recovered this month, recovered last
    month (for the trend line), and what is still outstanding."""
    today = today or _today()
    m_start = _month_start(today)
    m_end = _next_month(m_start)
    l_start = _prev_month(m_start)
    return {
        "month": m_start.strftime("%B"),
        "recovered_this_month": recovered_between(db, business_id, m_start, m_end),
        "recovered_last_month": recovered_between(db, business_id, l_start, m_start),
        "outstanding": outstanding_total(db, business_id),
    }
