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


def test_one_late_bill_among_many_paid_does_not_tank():
    # The owner's rule: paid many, missed one -> stays Reliable. 9 not-due bills,
    # 1 very overdue (70d), and a strong booked-payment history.
    bills = [{"outstanding": 5000, "invoice_date": "2026-08-05", "due_date": "2026-09-05"}
             for _ in range(9)]
    bills.append({"outstanding": 5000, "invoice_date": "2026-05-01", "due_date": "2026-06-01"})  # 70d late
    sc = _sc(
        bills=bills,
        receipts=[{"amount": 1000, "receipt_date": f"2026-0{m}-01"} for m in range(3, 8)])  # 5 payments
    assert sc["grade"] == "reliable"       # NOT risky, despite one 70-day-old bill
    assert sc["overdue_count"] == 1
    assert sc["max_days_late"] == 70


def test_mostly_overdue_with_little_history_is_risky():
    # More bad than good -> the ratio does not rescue.
    bills = [{"outstanding": 5000, "invoice_date": "2026-05-01", "due_date": "2026-06-01"}  # 70d
             for _ in range(5)]
    sc = _sc(bills=bills, receipts=[{"amount": 1000, "receipt_date": "2026-07-01"}])  # 1 payment
    assert sc["grade"] == "risky"


def test_very_old_straggler_with_good_history_is_watch_not_hidden():
    # Great payer but one bill 120d old -> surfaced as Watch (not Reliable, not Risky).
    bills = [{"outstanding": 5000, "invoice_date": "2026-08-05", "due_date": "2026-09-05"}
             for _ in range(9)]
    bills.append({"outstanding": 5000, "invoice_date": "2026-03-01", "due_date": "2026-04-01"})  # 131d
    sc = _sc(
        bills=bills,
        receipts=[{"amount": 1000, "receipt_date": f"2026-0{m}-01"} for m in range(3, 8)])
    assert sc["grade"] == "watch"


def test_scorecard_text_is_cleanly_spaced():
    sc = _sc(bills=[{"outstanding": 5000, "invoice_date": "2026-05-01", "due_date": "2026-06-01"}])
    txt = scorecard.scorecard_text(sc, "Ramesh")
    assert "Ramesh" in txt and "Slow payer" in txt
    assert "\n\n" in txt                    # blank line between blocks, not congested
