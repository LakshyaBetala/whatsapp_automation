"""Tests for the Today home aggregator (app/services/today.py) and the
receivables aging/DSO helper (app/services/aging.py). Pure - a tiny fake db
stub returns canned rows, so no network or Supabase is needed."""
import datetime as dt

from app.services import aging, today as today_svc

TODAY = dt.date(2026, 8, 19)


def test_aging_buckets_and_days_stuck():
    bills = [
        {"outstanding": 100000, "invoice_date": "2026-07-01", "due_date": "2026-07-31"},  # 19d over
        {"outstanding": 50000, "invoice_date": "2026-06-01", "due_date": "2026-07-01"},   # 49d over
        {"outstanding": 20000, "invoice_date": "2026-08-15", "due_date": "2026-09-14"},   # not due
        {"outstanding": 0, "invoice_date": "2026-01-01", "due_date": "2026-02-01"},        # skipped
    ]
    ag = aging.compute(bills, TODAY)
    assert ag["total"] == 170000
    by = {b["key"]: b for b in ag["buckets"]}
    assert by["current"]["amount"] == 20000
    assert by["d1_30"]["amount"] == 100000
    assert by["d31_60"]["amount"] == 50000
    assert ag["days_stuck"] > 0  # outstanding-weighted average age


# ── Minimal Supabase-query stub ───────────────────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._range = None
        self._eq = []
        self._in = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in.append((col, set(vals)))
        return self

    def gte(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def execute(self):
        rows = [r for r in self.store.get(self.table, [])
                if all(r.get(c) == v for c, v in self._eq)
                and all(r.get(c) in s for c, s in self._in)]
        if self._range is not None:
            lo, hi = self._range
            rows = rows[lo:hi + 1]
        return _Resp(rows)


class _DB:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _bill(**k):
    k.setdefault("business_id", "b1")
    k.setdefault("status", "overdue")
    return k


def _make_db():
    store = {
        "bills": [
            _bill(outstanding=100000, invoice_date="2026-07-01", due_date="2026-07-31",
                  client_id="c1", clients={"name": "RAJESH TRADERS", "whatsapp_number": "919812345678"}),
            _bill(outstanding=50000, invoice_date="2026-06-01", due_date="2026-07-01",
                  client_id="c2", clients={"name": "M/S KIRAN & CO", "whatsapp_number": None}),
            _bill(outstanding=20000, invoice_date="2026-08-15", due_date="2026-09-14",
                  client_id="c3", status="pending",
                  clients={"name": "NEW SHOP", "whatsapp_number": "919800000000"}),
        ],
        "tally_receipts": [
            {"business_id": "b1", "amount": 30000, "receipt_date": "2026-08-18"},
            {"business_id": "b1", "amount": 12000, "receipt_date": "2026-08-19"},
        ],
        "payment_promises": [],
    }
    return _DB(store)


def test_build_today_snapshot_is_honest():
    db = _make_db()
    biz = {"id": "b1", "business_name": "RISHAB TRADING"}
    snap = today_svc.build_today(db, "b1", biz, today=TODAY)

    assert snap["outstanding"]["total"] == 170000
    assert snap["outstanding"]["party_count"] == 3
    assert snap["money_in"]["yesterday"]["amount"] == 30000
    assert snap["money_in"]["today"]["amount"] == 12000
    # KIRAN owes and has no number -> exactly one unreachable party
    assert snap["no_number"]["count"] == 1
    # Only the two overdue parties are on the chase list; c3 is not yet due.
    assert snap["chase_overdue_count"] == 2
    # Biggest money at stake leads (gentle age boost keeps 100k/19d above 50k/49d).
    assert snap["chase"][0]["client_id"] == "c1"
    assert snap["chase"][0]["has_number"] is True
    assert snap["chase"][1]["client_id"] == "c2"
    assert snap["chase"][1]["has_number"] is False
    assert snap["dso"]["days_stuck"] > 0


def test_build_today_tolerates_empty_shop():
    db = _DB({"bills": [], "tally_receipts": [], "payment_promises": []})
    snap = today_svc.build_today(db, "b1", {"id": "b1", "business_name": "X"}, today=TODAY)
    assert snap["outstanding"]["total"] == 0
    assert snap["chase"] == []
    assert snap["no_number"]["count"] == 0
    assert snap["dso"]["days_stuck"] == 0
