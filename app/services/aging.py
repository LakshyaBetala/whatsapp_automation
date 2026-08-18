"""Receivables aging + DSO-style "money stuck" number.

The one number a distributor checks like a bathroom scale: *on average, how many
days is my money stuck?* Plus the classic aging buckets so the owner can see at a
glance how much of the outstanding is fresh vs genuinely old.

Everything here is derived from the shop's OWN open bills - no invented figures:
  - "days stuck" = outstanding-weighted average age since the invoice date. A
    big old bill drags the number up more than a small fresh one, which is what
    an owner intuitively means by "my money is stuck".
  - aging buckets are keyed on days PAST the due date (due_date already includes
    the party's credit period), so "overdue" is measured fairly.

Pure functions over a list of bill dicts, so the Today screen, the digest, and a
future trend snapshot all quote the same truth.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

# Buckets by days past the due date. "current" = not yet overdue.
BUCKET_DEFS = [
    ("current", "Not due yet", None, 0),
    ("d1_30", "1-30 days over", 1, 30),
    ("d31_60", "31-60 days over", 31, 60),
    ("d61_90", "61-90 days over", 61, 90),
    ("d90p", "90+ days over", 91, None),
]


def _d(x) -> Decimal:
    try:
        return Decimal(str(x if x is not None else 0))
    except Exception:
        return Decimal(0)


def _date(iso):
    try:
        return _dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def compute(open_bills: list[dict], today: _dt.date) -> dict:
    """Given open bills (each with outstanding / invoice_date / due_date),
    return {days_stuck, total, buckets:[{key,label,amount,count}]}.

    Best-effort: a bill with no usable outstanding is skipped; a bill with no
    date still counts toward the total but not toward the weighted age.
    """
    total = Decimal(0)
    weighted_age = Decimal(0)
    aged_base = Decimal(0)                      # outstanding that had a usable date
    buckets = {k: {"amount": Decimal(0), "count": 0} for k, *_ in BUCKET_DEFS}

    for b in open_bills:
        out = _d(b.get("outstanding"))
        if out <= 0:
            continue
        total += out

        inv = _date(b.get("invoice_date"))
        if inv is not None:
            age = max(0, (today - inv).days)
            weighted_age += out * Decimal(age)
            aged_base += out

        # Aging bucket by days past due (fall back to invoice age if no due date).
        due = _date(b.get("due_date"))
        over = (today - due).days if due is not None else (
            (today - inv).days if inv is not None else 0)
        key = "current"
        for k, _label, lo, hi in BUCKET_DEFS:
            if lo is None:                      # the "current" bucket
                continue
            if over >= lo and (hi is None or over <= hi):
                key = k
                break
        if over <= 0:
            key = "current"
        buckets[key]["amount"] += out
        buckets[key]["count"] += 1

    days_stuck = int(round(float(weighted_age / aged_base))) if aged_base > 0 else 0
    return {
        "days_stuck": days_stuck,
        "total": float(total),
        "buckets": [
            {"key": k, "label": label,
             "amount": float(buckets[k]["amount"]), "count": buckets[k]["count"]}
            for k, label, _lo, _hi in BUCKET_DEFS
        ],
    }
