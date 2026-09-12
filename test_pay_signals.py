"""Expected payments: a tap stays open until evidence arrives, and is never "paid"."""
import datetime as dt

from app.services import pay_signals as ps

BIZ = "94e2e0a7-2cc9-4bd4-b5a2-eca6ce924d17"
CID = "5a1c2d3e-4f50-4617-8899-aabbccddeeff"


class _Resp:
    def __init__(self, data): self.data = data


class _Tbl:
    """Tiny in-memory pay_link_events with the filters the service uses."""
    def __init__(self, store): self.store, self.filters, self.op, self.payload = store, [], None, None

    def select(self, *_): return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def eq(self, k, v): self.filters.append(lambda r: r.get(k) == v); return self
    def is_(self, k, v): self.filters.append(lambda r: r.get(k) is None); return self
    def in_(self, k, vs): self.filters.append(lambda r: r.get(k) in vs); return self
    def gte(self, k, v): self.filters.append(lambda r: str(r.get(k) or "") >= v); return self
    def lt(self, k, v): self.filters.append(lambda r: str(r.get(k) or "") < v); return self
    def order(self, *a, **k): return self
    def limit(self, n): return self

    def execute(self):
        rows = [r for r in self.store if all(f(r) for f in self.filters)]
        if self.op == "insert":
            row = dict(self.payload); row.setdefault("id", "e%d" % (len(self.store) + 1))
            row.setdefault("created_at", dt.datetime.now(dt.timezone.utc).isoformat())
            self.store.append(row); return _Resp([row])
        if self.op == "update":
            for r in rows: r.update(self.payload)
            return _Resp(rows)
        return _Resp(rows)


class DB:
    def __init__(self): self.store = []
    def table(self, name): assert name == "pay_link_events"; return _Tbl(self.store)


def test_a_new_tap_supersedes_the_customers_older_open_tap():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "INV 1 part")
    ps.record(db, BIZ, CID, "tapped", 9330, "INV 1")
    open_ = ps.open_taps(db, BIZ)
    assert len(open_) == 1 and open_[0]["amount"] == 9330 and open_[0]["note"] == "INV 1"
    assert [r["resolved_by"] for r in db.store if r.get("resolved_by")] == ["superseded"]


def test_a_tally_receipt_of_about_the_amount_closes_the_tap():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "INV 1 part")
    assert ps.resolve_for_receipt(db, BIZ, CID, 4960, "tally") == 1     # within 1%
    assert ps.open_taps(db, BIZ) == []
    assert db.store[0]["resolved_by"] == "tally"


def test_a_much_smaller_receipt_does_not_close_the_tap():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "")
    assert ps.resolve_for_receipt(db, BIZ, CID, 2000, "tally") == 0
    assert len(ps.open_taps(db, BIZ)) == 1


def test_a_receipt_for_another_party_is_ignored():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "")
    assert ps.resolve_for_receipt(db, BIZ, "someone-else", 5000, "tally") == 0


def test_owner_action_closes_whatever_the_amount():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "")
    assert ps.resolve_for_client(db, BIZ, CID, "owner") == 1
    assert db.store[0]["resolved_by"] == "owner"


def test_expiry_closes_only_old_taps():
    db = DB()
    ps.record(db, BIZ, CID, "tapped", 5000, "")
    db.store[0]["created_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=4)).isoformat()
    ps.record(db, BIZ, "other", "tapped", 100, "")
    assert ps.expire(db) == 1
    assert db.store[0]["resolved_by"] == "expired" and db.store[1].get("resolved_by") is None


def test_opened_events_never_appear_as_expected():
    db = DB()
    ps.record(db, BIZ, CID, "opened", 5000, "")
    assert ps.open_taps(db, BIZ) == []


def test_nothing_raises_without_a_database():
    assert ps.resolve_for_receipt(None, BIZ, CID, 1, "tally") == 0
    assert ps.expire(None) == 0
    ps.record(None, BIZ, CID, "tapped", 1)
