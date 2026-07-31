"""TEAM/support request log (app/services/support.py) - the owner-to-operator
channel the Command Center shows. Must never break the owner's message on a
missing table."""
from app.services import support as S


class _Q:
    def __init__(self, store): self.store = store; self.op = None; self.patch = None; self.f = {}
    def insert(self, row): self.op = "insert"; self.row = dict(row); return self
    def update(self, p): self.op = "update"; self.patch = dict(p); return self
    def select(self, *a, **k): self.op = "select"; return self
    def eq(self, f, v): self.f[f] = v; return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def execute(self):
        if self.op == "insert":
            self.row.setdefault("id", "r%d" % (len(self.store) + 1))
            self.store.append(self.row)
            return type("R", (), {"data": [self.row], "count": None})()
        if self.op == "update":
            hit = [r for r in self.store if all(r.get(k) == v for k, v in self.f.items())]
            for r in hit:
                r.update(self.patch)
            return type("R", (), {"data": hit, "count": None})()
        rows = [r for r in self.store if all(r.get(k) == v for k, v in self.f.items())]
        return type("R", (), {"data": rows, "count": len(rows)})()


class FakeDB:
    def __init__(self): self.rows = []
    def table(self, n): return _Q(self.rows)


class BoomDB:
    def table(self, n): raise RuntimeError("no table")


def test_record_and_list():
    db = FakeDB()
    row = S.record(db, business_id="b1", business_name="Rishab", from_number="9198", message="app slow")
    assert row and row["status"] == "open" and row["message"] == "app slow"
    reqs = S.list_recent(db)
    assert len(reqs) == 1 and reqs[0]["business_name"] == "Rishab"
    assert S.open_count(db) == 1


def test_resolve_and_reopen():
    db = FakeDB()
    r = S.record(db, business_id="b1", business_name="R", from_number="9198", message="x")
    assert S.resolve(db, r["id"], "resolved") is True
    assert db.rows[0]["status"] == "resolved" and db.rows[0].get("resolved_at")
    assert S.open_count(db) == 0
    assert S.resolve(db, r["id"], "open") is True
    assert db.rows[0]["status"] == "open"


def test_open_first_ordering():
    db = FakeDB()
    S.record(db, business_id="b1", business_name="A", from_number="1", message="old")
    r2 = S.record(db, business_id="b1", business_name="B", from_number="2", message="done")
    S.resolve(db, r2["id"])
    S.record(db, business_id="b1", business_name="C", from_number="3", message="new open")
    order = [r["status"] for r in S.list_recent(db)]
    assert order[0] == "open" and order[-1] == "resolved"    # open ones surface first


def test_missing_table_is_safe():
    assert S.record(BoomDB(), business_id="b1", business_name="R", from_number="9", message="x") is None
    assert S.list_recent(BoomDB()) == []
    assert S.open_count(BoomDB()) == 0
    assert S.resolve(BoomDB(), "r1") is False
