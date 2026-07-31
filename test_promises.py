"""Promise-to-Pay state service (payment_promises table).

Covers create + supersede (one open per client), held_now (only live holds),
close_for_client, due_followups, mark, and graceful degradation when the table
is missing (migration 028 not applied).
"""
import datetime as dt

from app.services import promises


def _future(days=1):
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)


def _past(days=1):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


# ── an in-memory payment_promises table backing the query chain ─────────────
class _R:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._op = None; self._patch = None; self._ins = None
        self._filters = []; self._order = None; self._desc = False; self._limit = None

    def insert(self, row): self._op = "insert"; self._ins = row; return self
    def update(self, patch): self._op = "update"; self._patch = dict(patch); return self
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, f, v): self._filters.append(("eq", f, v)); return self
    def in_(self, f, v): self._filters.append(("in", f, v)); return self
    def gt(self, f, v): self._filters.append(("gt", f, v)); return self
    def lte(self, f, v): self._filters.append(("lte", f, v)); return self
    def is_(self, f, v): self._filters.append(("is", f, v)); return self
    def order(self, f, desc=False): self._order = f; self._desc = desc; return self
    def limit(self, n): self._limit = n; return self

    def _match(self, r):
        for op, f, v in self._filters:
            rv = r.get(f)
            if op == "eq" and rv != v: return False
            if op == "in" and rv not in v: return False
            if op == "gt" and not (rv is not None and str(rv) > str(v)): return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if op == "is" and v == "null" and rv is not None: return False
        return True

    def execute(self):
        if self._op == "insert":
            import uuid
            row = dict(self._ins)
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", dt.datetime.now(dt.timezone.utc).isoformat())
            self.rows.append(row)
            return _R([row])
        hit = [r for r in self.rows if self._match(r)]
        if self._op == "update":
            for r in hit:
                r.update(self._patch)
            return _R(hit)
        if self._order:
            hit = sorted(hit, key=lambda r: str(r.get(self._order) or ""), reverse=self._desc)
        if self._limit:
            hit = hit[: self._limit]
        return _R([dict(r) for r in hit])


class FakeDB:
    def __init__(self):
        self.rows = []
    def table(self, name):
        return FakeTable(self.rows)


class BoomDB:
    def table(self, *a, **k):
        raise RuntimeError("relation payment_promises does not exist")


# ── tests ───────────────────────────────────────────────────────────────────
def test_create_and_held_now():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_future(3))
    hn = promises.held_now(db, ["b1", "b2"])
    assert hn.get("b1") == {"c1"} and "b2" not in hn


def test_supersede_keeps_one_open():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_future(3))
    promises.create(db, "b1", "c1", kind="promise", hold_until=_future(5), promise_date="2026-08-05")
    opens = [r for r in db.rows if r["status"] == "open" and r["client_id"] == "c1"]
    assert len(opens) == 1 and opens[0]["kind"] == "promise"
    assert promises.held_now(db, ["b1"]) == {"b1": {"c1"}}


def test_expired_hold_is_not_held_now():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_past(1))
    assert promises.held_now(db, ["b1"]) == {}


def test_close_for_client_releases_hold():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_future(3))
    assert promises.close_for_client(db, "b1", "c1", "cancelled") is True
    assert promises.held_now(db, ["b1"]) == {}
    assert promises.find_open(db, "b1", "c1") is None


def test_due_followups_and_mark():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="promise", hold_until=_past(1), promise_date="2026-07-24")
    promises.create(db, "b1", "c2", kind="paid_claim", hold_until=_future(2))  # not due yet
    due = promises.due_followups(db)
    assert [d["client_id"] for d in due] == ["c1"]
    promises.mark(db, due[0]["id"], "broken", followup=True)
    assert promises.due_followups(db) == []   # followup_sent_at set + status changed
    assert [r for r in db.rows if r["client_id"] == "c1"][0]["status"] == "broken"


def test_open_for_business():
    db = FakeDB()
    promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_future(1))
    promises.create(db, "b1", "c2", kind="promise", hold_until=_future(2), promise_date="2026-08-01")
    rows = promises.open_for_business(db, "b1")
    assert {r["client_id"] for r in rows} == {"c1", "c2"}


def test_missing_table_degrades_gracefully():
    db = BoomDB()
    assert promises.create(db, "b1", "c1", kind="paid_claim", hold_until=_future(1)) is None
    assert promises.held_now(db, ["b1"]) == {}
    assert promises.due_followups(db) == []
    assert promises.open_for_business(db, "b1") == []
    assert promises.find_open(db, "b1", "c1") is None
    assert promises.close_for_client(db, "b1", "c1", "cancelled") is False
