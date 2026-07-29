"""Payment-entry allocation + queue (app/services/receipts_queue.py).

The money math must be exact and cover every outlier: exact clear, partial,
overpayment (advance), zero/closed bills, no bills, paisa precision. The queue
must never lose a payment or double-handle one.
"""
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import receipts_queue as rq


def _bills(*pairs):
    return [{"ref": r, "outstanding": o} for r, o in pairs]


# ── allocate_fifo ────────────────────────────────────────────────────────────
def test_exact_clear_of_one_bill():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 5000)), 5000)
    assert allocs == [{"ref": "A", "amount": Decimal("5000.00")}]
    assert on_acct == Decimal("0.00")


def test_partial_payment_hits_oldest_only():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 5000), ("B", 3000)), 2000)
    assert allocs == [{"ref": "A", "amount": Decimal("2000.00")}]
    assert on_acct == 0


def test_spills_across_bills_oldest_first():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 550), ("B", 3000)), 2000)
    assert allocs == [{"ref": "A", "amount": Decimal("550.00")},
                      {"ref": "B", "amount": Decimal("1450.00")}]
    assert on_acct == 0


def test_overpayment_is_on_account():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 1000), ("B", 500)), 2000)
    assert [a["amount"] for a in allocs] == [Decimal("1000.00"), Decimal("500.00")]
    assert on_acct == Decimal("500.00")            # advance, nothing left to clear


def test_no_open_bills_is_all_on_account():
    allocs, on_acct = rq.allocate_fifo([], 1500)
    assert allocs == [] and on_acct == Decimal("1500.00")


def test_skips_zero_and_negative_bills():
    allocs, _ = rq.allocate_fifo(_bills(("A", 0), ("B", -100), ("C", 800)), 500)
    assert allocs == [{"ref": "C", "amount": Decimal("500.00")}]


def test_paisa_precision():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", "33.33"), ("B", "66.67")), "100.00")
    assert [a["amount"] for a in allocs] == [Decimal("33.33"), Decimal("66.67")]
    assert on_acct == Decimal("0.00")


def test_never_over_allocates_a_bill():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 100)), 100)
    assert allocs[0]["amount"] == Decimal("100.00") and on_acct == 0


def test_zero_amount_allocates_nothing():
    allocs, on_acct = rq.allocate_fifo(_bills(("A", 100)), 0)
    assert allocs == [] and on_acct == Decimal("0.00")


# ── the queue ────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, sink, rows):
        self.sink = sink; self.rows = rows; self._f = []; self._patch = None; self._op = None
    def insert(self, row): self._op = "insert"; self.sink.append(row); self._row = row; return self
    def update(self, patch): self._op = "update"; self._patch = dict(patch); return self
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, f, v): self._f.append((f, v)); return self
    def order(self, *a, **k): return self
    def execute(self):
        if self._op == "insert":
            return type("R", (), {"data": [self._row]})()
        if self._op == "update":
            for r in self.rows:
                r.update(self._patch)
            return type("R", (), {"data": self.rows})()
        out = [r for r in self.rows if all(r.get(f) == v for f, v in self._f)]
        return type("R", (), {"data": out})()


class FakeDB:
    def __init__(self, rows=None):
        self.inserted = []; self.rows = rows or []
    def table(self, name):
        return _Q(self.inserted, self.rows)


class BoomDB:
    def table(self, name):
        raise RuntimeError("down")


def test_create_pending_stores_the_receipt():
    db = FakeDB()
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="M/S RAMESH TRADERS",
                            party_display="Ramesh Traders", amount=500,
                            deposit_ledger="HDFC BANK", receipt_date="2026-07-29")
    assert out["party_ledger"] == "M/S RAMESH TRADERS"
    assert out["amount"] == 500.0 and out["deposit_ledger"] == "HDFC BANK"
    assert out["status"] == "pending" and out["receipt_date"] == "2026-07-29"


def test_create_pending_defaults_cash_and_today():
    db = FakeDB()
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="P",
                            party_display="P", amount=100)
    assert out["deposit_ledger"] == "CASH" and out["receipt_date"]


def test_create_pending_rejects_nonpositive():
    for bad in (0, -5):
        with pytest.raises(ValueError):
            rq.create_pending(FakeDB(), "b1", client_id="c1", party_ledger="P",
                              party_display="P", amount=bad)


def test_create_pending_survives_db_error():
    assert rq.create_pending(BoomDB(), "b1", client_id="c1", party_ledger="P",
                             party_display="P", amount=100) is None


def test_list_pending_only_pending():
    rows = [{"business_id": "b1", "status": "pending", "amount": 1},
            {"business_id": "b1", "status": "posted", "amount": 2}]
    out = rq.list_pending(FakeDB(rows), "b1")
    assert len(out) == 1 and out[0]["amount"] == 1


def test_mark_posted_keeps_voucher_id():
    rows = [{"id": "p1", "status": "pending"}]
    db = FakeDB(rows)
    assert rq.mark(db, "p1", "posted", voucher_id="V-123") is True
    assert rows[0]["status"] == "posted" and rows[0]["tally_voucher_id"] == "V-123"


def test_mark_failed_records_error():
    rows = [{"id": "p1", "status": "pending"}]
    db = FakeDB(rows)
    rq.mark(db, "p1", "failed", error="Ledger not found")
    assert rows[0]["status"] == "failed" and rows[0]["error"] == "Ledger not found"
