"""Monthly Recovery Report Card: honest numbers, clean spacing."""
import datetime as dt
from decimal import Decimal


class _Q:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Q(self.tables.get(name, []))


AUG_END = dt.date(2026, 8, 31)


def test_card_shows_recovered_and_up_trend(monkeypatch):
    from app.services import proof, report_card
    monkeypatch.setattr(proof, "build_proof", lambda db, bid, today=None: {
        "month": "August", "recovered_this_month": Decimal(120000),
        "recovered_last_month": Decimal(100000), "outstanding": Decimal(540000)})
    db = FakeDB({"tally_receipts": [{"id": 1}, {"id": 2}, {"id": 3}]})
    card = report_card.build_card(db, "biz", "RISHAB TRADING", today=AUG_END)
    assert "Report Card" in card and "August" in card
    assert "1,20,000" in card                 # recovered this month
    assert "Payments booked: 3" in card
    assert "Up" in card and "1,00,000" in card  # trend vs last month
    assert "5,40,000" in card                 # still outstanding


def test_card_zero_month_is_honest(monkeypatch):
    from app.services import proof, report_card
    monkeypatch.setattr(proof, "build_proof", lambda db, bid, today=None: {
        "month": "August", "recovered_this_month": Decimal(0),
        "recovered_last_month": Decimal(0), "outstanding": Decimal(1000)})
    card = report_card.build_card(FakeDB({"tally_receipts": []}), "biz", "Shop", today=AUG_END)
    assert "No payments" in card              # never fabricates a number
    assert "1,000" in card


def test_card_is_cleanly_spaced(monkeypatch):
    """Blank lines separate the blocks - never a congested wall of text."""
    from app.services import proof, report_card
    monkeypatch.setattr(proof, "build_proof", lambda db, bid, today=None: {
        "month": "August", "recovered_this_month": Decimal(5000),
        "recovered_last_month": Decimal(0), "outstanding": Decimal(2000)})
    card = report_card.build_card(FakeDB({"tally_receipts": [{"id": 1}]}), "biz", "Shop", today=AUG_END)
    assert "\n\n" in card                     # spacing present
