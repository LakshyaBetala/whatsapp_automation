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
        self.sink = sink; self.rows = rows; self._f = []; self._in = []
        self._patch = None; self._op = None; self._limit = None
    def insert(self, row): self._op = "insert"; self.sink.append(row); self._row = row; return self
    def update(self, patch): self._op = "update"; self._patch = dict(patch); return self
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, f, v): self._f.append((f, v)); return self
    def in_(self, f, vals): self._in.append((f, list(vals))); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._limit = n; return self
    def _match(self, r):
        return (all(r.get(f) == v for f, v in self._f)
                and all(r.get(f) in vals for f, vals in self._in))
    def execute(self):
        if self._op == "insert":
            return type("R", (), {"data": [self._row]})()
        if self._op == "update":
            changed = [r for r in self.rows if self._match(r)]
            for r in changed:
                r.update(self._patch)
            return type("R", (), {"data": changed})()
        out = [r for r in self.rows if self._match(r)]
        if self._limit is not None:
            out = out[:self._limit]
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


class _OcrRejectQ(_Q):
    """Insert/update fails if the row carries any ocr_* column - simulates a DB
    where migration 038 (the OCR hint columns) hasn't been applied yet."""
    def insert(self, row):
        if any(k.startswith("ocr_") for k in row):
            raise RuntimeError('column "ocr_payer" does not exist')
        return super().insert(row)
    def update(self, patch):
        if any(k.startswith("ocr_") for k in patch):
            raise RuntimeError('column "ocr_payer" does not exist')
        return super().update(patch)


class OcrRejectDB(FakeDB):
    def table(self, name):
        return _OcrRejectQ(self.inserted, self.rows)


def test_create_pending_survives_missing_ocr_columns():
    # A screenshot ASVA can read carries ocr_* hints. If migration 038 lags, the
    # first insert fails - but the PAYMENT must still be queued (retry without the
    # suggestion columns). Losing a real payment over a prefill would be the worst
    # possible failure of the whole capture pipeline.
    db = OcrRejectDB()
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="RAMESH",
                            party_display="Ramesh", amount=310,
                            ocr_payer="Ramesh", ocr_ref="123456789012", ocr_confidence=0.95)
    assert out is not None
    assert out["amount"] == 310.0 and out["status"] == "pending"
    assert "ocr_payer" not in out                 # dropped, but the receipt survived


def test_create_pending_dedup_survives_missing_ocr_columns():
    # Same, on the dedup/update path (an open receipt already exists for the party).
    rows = [{"id": "p1", "business_id": "b1", "client_id": "c1", "status": "pending",
             "amount": 100.0, "deposit_ledger": "Cash"}]
    db = OcrRejectDB(rows)
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="R",
                            party_display="R", amount=250, ocr_ref="999", ocr_confidence=0.8)
    assert out["id"] == "p1" and rows[0]["amount"] == 250.0
    assert "ocr_ref" not in rows[0]


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


def test_mark_posted_keeps_allocation():
    rows = [{"id": "p1", "status": "pending"}]
    db = FakeDB(rows)
    alloc = [{"ref": "INV1", "amount": 500}]
    rq.mark(db, "p1", "posted", voucher_id="V-9", allocation=alloc)
    assert rows[0]["allocation"] == alloc and rows[0]["tally_voucher_id"] == "V-9"


# ── confirm / queues (C7) ─────────────────────────────────────────────────────
def test_confirm_moves_pending_to_confirmed_and_edits():
    rows = [{"id": "p1", "business_id": "b1", "status": "pending",
             "amount": 5000.0, "deposit_ledger": "Cash"}]
    db = FakeDB(rows)
    out = rq.confirm(db, "b1", "p1", amount=4800, deposit_ledger="HDFC BANK")
    assert out is not None
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["amount"] == 4800.0 and rows[0]["deposit_ledger"] == "HDFC BANK"
    assert rows[0].get("confirmed_at")


def test_confirm_rejects_nonpositive_amount():
    db = FakeDB([{"id": "p1", "business_id": "b1", "status": "pending", "amount": 10}])
    with pytest.raises(ValueError):
        rq.confirm(db, "b1", "p1", amount=0)


def test_confirm_is_tenant_scoped():
    # A receipt belonging to another business must not be confirmable.
    rows = [{"id": "p1", "business_id": "OTHER", "status": "pending", "amount": 10}]
    db = FakeDB(rows)
    assert rq.confirm(db, "b1", "p1") is None
    assert rows[0]["status"] == "pending"


def test_list_confirmed_only_confirmed():
    rows = [{"business_id": "b1", "status": "confirmed", "amount": 1, "confirmed_at": "t"},
            {"business_id": "b1", "status": "pending", "amount": 2},
            {"business_id": "b1", "status": "posted", "amount": 3}]
    out = rq.list_confirmed(FakeDB(rows), "b1")
    assert [r["amount"] for r in out] == [1]


def test_list_for_owner_shows_actionable_only():
    rows = [{"business_id": "b1", "status": "pending", "amount": 1},
            {"business_id": "b1", "status": "confirmed", "amount": 2},
            {"business_id": "b1", "status": "failed", "amount": 3},
            {"business_id": "b1", "status": "posted", "amount": 4},
            {"business_id": "b1", "status": "skipped", "amount": 5}]
    out = rq.list_for_owner(FakeDB(rows), "b1")
    assert sorted(r["amount"] for r in out) == [1, 2, 3]


def test_get_by_id_is_tenant_scoped():
    rows = [{"id": "p1", "business_id": "b1", "status": "pending"}]
    db = FakeDB(rows)
    assert rq.get_by_id(db, "b1", "p1")["id"] == "p1"
    assert rq.get_by_id(db, "OTHER", "p1") is None


def test_deposit_ledgers_roundtrip_and_cash_fallback():
    rows = [{"id": "b1"}]
    db = FakeDB(rows)
    rq.set_deposit_ledgers(db, "b1", ["HDFC Bank", "  ", "ICICI Bank"])
    assert rows[0]["tally_deposit_ledgers"] == ["HDFC Bank", "ICICI Bank"]
    got = rq.get_deposit_ledgers(db, "b1")
    assert got[0] == "Cash" and "HDFC Bank" in got   # Cash always offered


def test_get_deposit_ledgers_defaults_to_cash_when_empty():
    assert rq.get_deposit_ledgers(FakeDB([{"id": "b1"}]), "b1") == ["Cash"]


# ── double-post guards ────────────────────────────────────────────────────────
def test_enqueue_dedups_open_receipt_for_same_client():
    # Two entry points (PAID + "Enter payment") for the SAME party must not create
    # two open receipts - the second updates the first (the double-post cause).
    rows = [{"id": "p1", "business_id": "b1", "client_id": "c1", "status": "pending",
             "amount": 5000.0, "deposit_ledger": "Cash"}]
    db = FakeDB(rows)
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="RAMESH",
                            party_display="Ramesh", amount=4800, deposit_ledger="HDFC")
    assert out["id"] == "p1"                       # updated the existing row
    assert rows[0]["amount"] == 4800.0 and rows[0]["deposit_ledger"] == "HDFC"
    assert len([r for r in rows if r["client_id"] == "c1"]) == 1   # still ONE row
    assert db.inserted == []                       # nothing new inserted


def test_enqueue_after_posted_creates_a_fresh_row():
    # A posted receipt does not block a genuine second payment later.
    rows = [{"id": "p1", "business_id": "b1", "client_id": "c1", "status": "posted",
             "amount": 5000.0}]
    db = FakeDB(rows)
    out = rq.create_pending(db, "b1", client_id="c1", party_ledger="R",
                            party_display="R", amount=1000)
    assert out["status"] == "pending" and db.inserted            # a new row was made


def test_claim_confirmed_flips_to_posting_once():
    rows = [{"id": "p1", "business_id": "b1", "status": "confirmed", "amount": 1},
            {"id": "p2", "business_id": "b1", "status": "confirmed", "amount": 2},
            {"id": "p3", "business_id": "b1", "status": "pending", "amount": 3}]
    db = FakeDB(rows)
    claimed = rq.claim_confirmed(db, "b1")
    assert {r["id"] for r in claimed} == {"p1", "p2"}
    assert rows[0]["status"] == "posting" and rows[1]["status"] == "posting"
    # a second claim returns nothing (already posting -> never posted twice)
    assert rq.claim_confirmed(db, "b1") == []
