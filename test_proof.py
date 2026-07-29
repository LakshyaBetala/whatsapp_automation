"""Proof-of-value metric (app/services/proof.py) + the RECOVERED bot reply.

The number that renews and refers: "ASVA recovered Rs X this month." Recovered =
receipts booked in the period; the rest is live open outstanding. Best-effort
reads must never raise.
"""
import asyncio
import datetime as _dt
import sys
from decimal import Decimal
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import proof


class _Q:
    def __init__(self, rows):
        self.rows = rows
        self._f = []
    def select(self, *a, **k): return self
    def eq(self, f, v): self._f.append(("eq", f, v)); return self
    def in_(self, f, v): self._f.append(("in", f, list(v))); return self
    def gte(self, f, v): self._f.append(("gte", f, v)); return self
    def lt(self, f, v): self._f.append(("lt", f, v)); return self
    def _ok(self, r):
        for op, f, v in self._f:
            if op == "eq" and r.get(f) != v: return False
            if op == "in" and r.get(f) not in v: return False
            if op == "gte" and not (str(r.get(f)) >= v): return False
            if op == "lt" and not (str(r.get(f)) < v): return False
        return True
    def execute(self):
        return type("R", (), {"data": [r for r in self.rows if self._ok(r)]})()


class FakeDB:
    def __init__(self, receipts=None, bills=None):
        self.tables = {"tally_receipts": receipts or [], "bills": bills or []}
    def table(self, name):
        return _Q(self.tables.get(name, []))


class BoomDB:
    def table(self, name):
        raise RuntimeError("db down")


TODAY = _dt.date(2026, 7, 20)


def _receipt(biz, amount, d):
    return {"business_id": biz, "amount": amount, "receipt_date": d}


def test_recovered_this_month_sums_only_this_month():
    db = FakeDB(receipts=[
        _receipt("b1", 40000, "2026-07-05"),
        _receipt("b1", 10000, "2026-07-19"),
        _receipt("b1", 99999, "2026-06-30"),   # last month, excluded
        _receipt("b2", 5000, "2026-07-10"),     # other business, excluded
    ])
    p = proof.build_proof(db, "b1", today=TODAY)
    assert p["recovered_this_month"] == Decimal("50000")
    assert p["recovered_last_month"] == Decimal("99999")
    assert p["month"] == "July"


def test_outstanding_sums_open_bills_only():
    db = FakeDB(bills=[
        {"business_id": "b1", "outstanding": 30000, "status": "overdue"},
        {"business_id": "b1", "outstanding": 5000, "status": "partial"},
        {"business_id": "b1", "outstanding": 999, "status": "paid"},     # closed, excluded
        {"business_id": "b2", "outstanding": 7000, "status": "pending"},  # other biz
    ])
    p = proof.build_proof(db, "b1", today=TODAY)
    assert p["outstanding"] == Decimal("35000")


def test_january_rolls_back_to_december():
    db = FakeDB(receipts=[
        _receipt("b1", 1000, "2026-01-04"),
        _receipt("b1", 2000, "2025-12-15"),
    ])
    p = proof.build_proof(db, "b1", today=_dt.date(2026, 1, 10))
    assert p["recovered_this_month"] == Decimal("1000")
    assert p["recovered_last_month"] == Decimal("2000")


def test_never_raises_on_db_error():
    p = proof.build_proof(BoomDB(), "b1", today=TODAY)
    assert p["recovered_this_month"] == Decimal(0) and p["outstanding"] == Decimal(0)


# ── the RECOVERED bot reply ──────────────────────────────────────────────────
def test_recovered_reply_english(monkeypatch):
    from app.services import bot
    monkeypatch.setattr(bot, "require_db", lambda: FakeDB(
        receipts=[_receipt("biz1", 40000, "2026-07-05")],
        bills=[{"business_id": "biz1", "outstanding": 12000, "status": "overdue"}]))
    monkeypatch.setattr(proof, "_today", lambda: TODAY)
    out = asyncio.run(bot._handle_recovered("biz1", lang="english"))
    assert "40,000" in out and "12,000" in out and "recovered" in out.lower()


def test_recovered_reply_hinglish_and_zero(monkeypatch):
    from app.services import bot
    monkeypatch.setattr(bot, "require_db", lambda: FakeDB(
        receipts=[], bills=[{"business_id": "biz1", "outstanding": 8000, "status": "pending"}]))
    monkeypatch.setattr(proof, "_today", lambda: TODAY)
    out = asyncio.run(bot._handle_recovered("biz1", lang="hinglish"))
    assert "8,000" in out and "baaki" in out          # Hinglish zero-state copy
