"""Cash-in forecast: promised + due-this-week, nothing double-counted."""
import datetime as dt
from decimal import Decimal

from app.services import forecast


class _Q:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Q(self.tables.get(name, []))


TODAY = dt.date(2026, 8, 10)


def test_promised_and_due_no_double_count(monkeypatch):
    from app.services import promises
    monkeypatch.setattr(promises, "open_for_business",
                        lambda db, bid: [{"client_id": "c1", "kind": "paid_claim", "amount": 30000}])
    bills = [
        {"client_id": "c1", "outstanding": 30000, "due_date": "2026-08-12"},  # promised -> not double
        {"client_id": "c2", "outstanding": 15000, "due_date": "2026-08-14"},  # due this week
        {"client_id": "c3", "outstanding": 99999, "due_date": "2026-12-01"},  # far off -> excluded
    ]
    f = forecast.cash_in_forecast(FakeDB({"bills": bills}), "biz", today=TODAY)
    assert f["promised"] == Decimal(30000)
    assert f["due_soon"] == Decimal(15000)          # c1 excluded (promised), c3 far
    assert f["total"] == Decimal(45000)
    assert f["promised_count"] == 1 and f["due_count"] == 1


def test_promise_with_null_amount_uses_outstanding(monkeypatch):
    from app.services import promises
    monkeypatch.setattr(promises, "open_for_business",
                        lambda db, bid: [{"client_id": "c1", "kind": "promise",
                                          "promise_date": "2026-08-12", "amount": None}])
    bills = [{"client_id": "c1", "outstanding": 8000, "due_date": "2026-08-30"}]  # due beyond horizon
    f = forecast.cash_in_forecast(FakeDB({"bills": bills}), "biz", today=TODAY)
    assert f["promised"] == Decimal(8000)           # falls back to open outstanding
    assert f["due_soon"] == Decimal(0)
    assert f["total"] == Decimal(8000)


def test_future_promise_beyond_horizon_is_excluded(monkeypatch):
    from app.services import promises
    monkeypatch.setattr(promises, "open_for_business",
                        lambda db, bid: [{"client_id": "c1", "kind": "promise",
                                          "promise_date": "2026-09-30", "amount": 5000}])
    f = forecast.cash_in_forecast(FakeDB({"bills": []}), "biz", today=TODAY)
    assert f["total"] == Decimal(0)


def test_empty_forecast(monkeypatch):
    from app.services import promises
    monkeypatch.setattr(promises, "open_for_business", lambda db, bid: [])
    f = forecast.cash_in_forecast(FakeDB({"bills": []}), "biz", today=TODAY)
    assert f["total"] == Decimal(0)


def test_forecast_line_blank_when_nothing():
    assert forecast.forecast_line({"total": 0}) == ""


def test_forecast_line_shows_amount():
    line = forecast.forecast_line(
        {"total": Decimal(45000), "promised": Decimal(30000),
         "due_soon": Decimal(15000), "horizon_days": 7})
    assert "COMING IN" in line and "7 days" in line
