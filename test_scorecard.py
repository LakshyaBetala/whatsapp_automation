"""Intra-shop reliability scorecard - accuracy is the point.

Every grade must follow only from provable data: terms-adjusted days past due,
recorded promise outcomes, and real receipts. A no-history party is never bad.
"""
import datetime as dt
from decimal import Decimal

from app.services import scorecard


class _Q:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Q(self.tables.get(name, []))


TODAY = dt.date(2026, 8, 10)
CLIENT = {"id": "c1", "name": "Ramesh", "tally_ledger_name": "Ramesh"}


def _sc(bills=None, promises=None, receipts=None):
    return scorecard.build_scorecard(
        FakeDB({"bills": bills or [], "payment_promises": promises or [],
                "tally_receipts": receipts or []}),
        "biz", CLIENT, today=TODAY)


def test_no_history_is_new_never_bad():
    # one open bill, not yet due, nothing else -> New (not Reliable, not Risky)
    sc = _sc(bills=[{"outstanding": 5000, "invoice_date": "2026-08-05", "due_date": "2026-09-05"}])
    assert sc["grade"] == "new"
    assert sc["reasons"] == ["Not enough payment history yet"]


def test_history_and_on_time_is_reliable():
    sc = _sc(
        bills=[{"outstanding": 5000, "invoice_date": "2026-08-05", "due_date": "2026-09-05"}],
        promises=[{"status": "kept"}])
    assert sc["grade"] == "reliable"
    assert sc["promises_kept"] == 1


def test_moderately_late_is_watch():
    # due 2026-07-25, today 2026-08-10 -> 16 days past due
    sc = _sc(bills=[{"outstanding": 5000, "invoice_date": "2026-07-01", "due_date": "2026-07-25"}])
    assert sc["grade"] == "watch"
    assert sc["max_days_late"] == 16          # exact, terms-adjusted


def test_very_late_is_risky():
    sc = _sc(bills=[{"outstanding": 5000, "invoice_date": "2026-05-01", "due_date": "2026-06-01"}])
    assert sc["grade"] == "risky"
    assert sc["max_days_late"] == 70


def test_two_broken_promises_is_risky_even_if_not_overdue():
    sc = _sc(
        bills=[{"outstanding": 5000, "invoice_date": "2026-08-05", "due_date": "2026-09-05"}],
        promises=[{"status": "broken"}, {"status": "broken"}])
    assert sc["grade"] == "risky"
    assert sc["promises_broken"] == 2


def test_receipts_totals_are_exact_no_fabricated_avg_days():
    sc = _sc(
        bills=[],
        receipts=[{"amount": 1000, "receipt_date": "2026-08-01"},
                  {"amount": 2000, "receipt_date": "2026-07-01"}])
    assert sc["payments_count"] == 2
    assert sc["total_recovered"] == Decimal(3000)
    assert sc["last_payment_date"] == "2026-08-01"
    assert "avg_days_to_pay" not in sc      # never fabricated


def test_scorecard_text_is_cleanly_spaced():
    sc = _sc(bills=[{"outstanding": 5000, "invoice_date": "2026-05-01", "due_date": "2026-06-01"}])
    txt = scorecard.scorecard_text(sc, "Ramesh")
    assert "Ramesh" in txt and "Slow payer" in txt
    assert "\n\n" in txt                    # blank line between blocks, not congested
