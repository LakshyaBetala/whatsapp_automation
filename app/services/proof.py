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


def billed_between(db, business_id: str, start: _dt.date, end: _dt.date) -> Decimal:
    """Sum of bills raised in [start, end). The denominator nobody had.

    Recovered on its own cannot tell "a quiet month" from "ASVA stopped
    working": both look like a small number. Against what the shop actually
    billed, the difference is obvious.
    """
    try:
        rows = (db.table("bills").select("amount")
                .eq("business_id", business_id)
                .gte("invoice_date", start.isoformat())
                .lt("invoice_date", end.isoformat()).execute()).data or []
        return sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal(0))
    except Exception:
        return Decimal(0)


def collection_pct(collected: Decimal, billed: Decimal) -> int | None:
    """Collected as a percentage of billed. None when there is nothing to
    divide by, which is not the same as zero and must not read as a failure."""
    if not billed or billed <= 0:
        return None
    return int(round(float(collected) / float(billed) * 100))


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
    rec_now = recovered_between(db, business_id, m_start, m_end)
    rec_prev = recovered_between(db, business_id, l_start, m_start)
    billed_now = billed_between(db, business_id, m_start, m_end)
    billed_prev = billed_between(db, business_id, l_start, m_start)
    return {
        "month": m_start.strftime("%B"),
        "recovered_this_month": rec_now,
        "recovered_last_month": rec_prev,
        "billed_this_month": billed_now,
        "billed_last_month": billed_prev,
        # The one number that says whether ASVA is working: of what the shop
        # billed, how much came back.
        "collected_pct": collection_pct(rec_now, billed_now),
        "collected_pct_last_month": collection_pct(rec_prev, billed_prev),
        "outstanding": outstanding_total(db, business_id),
    }
