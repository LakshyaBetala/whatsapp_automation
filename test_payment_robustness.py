"""Robustness of the payment -> Tally queue (app/services/receipts_queue.py).

The promise the owner must be able to trust: a payment they enter is NEVER lost,
NEVER posts to Tally twice, and a FAILED post can always be retried. This file
drives the queue through an in-memory Postgres stand-in and fuzzes long random
sequences of the exact operations the app and the agent perform:

  create_pending -> confirm -> claim_confirmed -> mark(posted/failed) -> confirm(retry)

Invariants asserted across every run:
  * no payment id ever vanishes (always fetchable) - nothing is lost
  * a row in 'posting'/'posted' is never handed to a second claim - no double post
  * a 'failed' row can be re-confirmed and re-posted - retry always works
"""
import datetime as _dt
import random
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import receipts_queue as rq


# ── an in-memory stand-in for the Supabase query builder over one table ───────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store):
        self._s = store
        self._filters = []          # list of (op, field, value)
        self._or = None
        self._order = None
        self._desc = False
        self._limit = None
        self._mode = None           # 'select' | 'insert' | 'update'
        self._payload = None

    # builders
    def select(self, *a, **k): self._mode = "select"; return self
    def insert(self, row): self._mode = "insert"; self._payload = dict(row); return self
    def update(self, patch): self._mode = "update"; self._payload = dict(patch); return self
    def eq(self, f, v): self._filters.append(("eq", f, v)); return self

    def in_(self, f, vals): self._filters.append(("in", f, list(vals))); return self

    def or_(self, expr): self._or = expr; return self
    def order(self, f, desc=False): self._order = f; self._desc = desc; return self
    def limit(self, n): self._limit = n; return self

    # exec
    def _match(self, row):
        for op, f, v in self._filters:
            if op == "eq" and row.get(f) != v:
                return False
            if op == "in" and row.get(f) not in v:
                return False
        if self._or:
            # only shape used: "posting_at.is.null,posting_at.lt.<cutoff>"
            ok = False
            for clause in self._or.split(","):
                parts = clause.split(".")
                field = parts[0]
                if parts[1] == "is" and parts[2] == "null":
                    ok = ok or row.get(field) is None
                elif parts[1] == "lt":
                    cutoff = ".".join(parts[2:])
                    val = row.get(field)
                    ok = ok or (val is not None and str(val) < cutoff)
            if not ok:
                return False
        return True

    def execute(self):
        if self._mode == "insert":
            row = self._payload
            row.setdefault("id", "r%d" % (len(self._s.rows) + 1))
            row.setdefault("created_at", self._s.clock())
            self._s.rows.append(row)
            return _Result([dict(row)])
        rows = [r for r in self._s.rows if self._match(r)]
        if self._order:
            rows.sort(key=lambda r: (r.get(self._order) is None, r.get(self._order)),
                      reverse=self._desc)
        if self._mode == "update":
            changed = []
            for r in rows:
                r.update(self._payload)
                changed.append(dict(r))
            return _Result(changed)
        # select
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result([dict(r) for r in rows])


class FakeDB:
    def __init__(self):
        self.rows = []
        self._t = 0

    def clock(self):
        # strictly increasing ISO-ish timestamps so ordering is deterministic
        self._t += 1
        return "2026-08-27T00:00:%02d+00:00" % (self._t % 60) + (":%06d" % self._t)

    def table(self, name):
        assert name == "pending_receipts"
        return _Query(self)


BID = "biz-1"


def _open(db, client_id="c1", amount=1000):
    return rq.create_pending(db, BID, client_id=client_id, party_ledger=client_id,
                             party_display=client_id, amount=amount)


# ── the retry bug: a FAILED receipt must be re-confirmable ────────────────────
def test_failed_receipt_can_be_reconfirmed():
    db = FakeDB()
    row = _open(db)
    assert rq.confirm(db, BID, row["id"]) is not None            # pending -> confirmed
    rq.mark(db, row["id"], "failed", error="Tally busy")
    again = rq.confirm(db, BID, row["id"])                       # failed -> confirmed
    assert again is not None
    assert again["status"] == "confirmed"


def test_confirm_when_already_inflight_is_idempotent_success():
    db = FakeDB()
    row = _open(db)
    rq.confirm(db, BID, row["id"])
    for st in ("confirmed", "posting", "posted"):
        db.rows[0]["status"] = st
        out = rq.confirm(db, BID, row["id"])
        assert out is not None and out["status"] == st           # never a false failure


def test_confirm_missing_row_returns_none():
    db = FakeDB()
    assert rq.confirm(db, BID, "nope") is None


# ── dedup: entering the same payment twice keeps ONE open row ─────────────────
def test_create_pending_dedups_same_client():
    db = FakeDB()
    a = _open(db, "c1", 500)
    b = _open(db, "c1", 900)                                     # owner re-enters
    assert a["id"] == b["id"]                                    # updated, not duplicated
    open_rows = [r for r in db.rows if r["status"] in ("pending", "confirmed")]
    assert len(open_rows) == 1
    assert open_rows[0]["amount"] == 500.0 or open_rows[0]["amount"] == 900.0


def test_create_pending_separate_clients_are_separate_rows():
    db = FakeDB()
    _open(db, "c1"); _open(db, "c2")
    assert len({r["id"] for r in db.rows}) == 2


# ── claim / reclaim: never hand the same receipt to two posts ─────────────────
def test_claim_flips_confirmed_to_posting_once():
    db = FakeDB()
    row = _open(db); rq.confirm(db, BID, row["id"])
    first = rq.claim_confirmed(db, BID)
    assert len(first) == 1 and first[0]["status"] == "posting"
    second = rq.claim_confirmed(db, BID)                         # nothing left to claim
    assert second == []


def test_stale_posting_is_reclaimed_but_fresh_is_left():
    db = FakeDB()
    stale = _open(db, "c1"); rq.confirm(db, BID, stale["id"])
    db.rows[0]["status"] = "posting"; db.rows[0]["posting_at"] = None   # legacy stuck row
    n = rq.reclaim_stale_posting(db, BID)
    assert n == 1 and db.rows[0]["status"] == "confirmed"
    # a freshly-claimed row (posting_at = now) must NOT be reclaimed
    fresh = _open(db, "c2"); rq.confirm(db, BID, fresh["id"])
    rq.claim_confirmed(db, BID)
    now_row = [r for r in db.rows if r["client_id"] == "c2"][0]
    assert now_row["status"] == "posting"
    assert rq.reclaim_stale_posting(db, BID) == 0


# ── full lifecycles ──────────────────────────────────────────────────────────
def test_happy_path_posts_exactly_once():
    db = FakeDB()
    row = _open(db); rq.confirm(db, BID, row["id"])
    claimed = rq.claim_confirmed(db, BID)
    assert len(claimed) == 1
    rq.mark(db, row["id"], "posted", voucher_id="V1")
    assert db.rows[0]["status"] == "posted"
    assert rq.claim_confirmed(db, BID) == []                     # never re-posted


def test_retry_lifecycle_posted_after_one_failure():
    db = FakeDB()
    row = _open(db); rq.confirm(db, BID, row["id"])
    rq.claim_confirmed(db, BID)
    rq.mark(db, row["id"], "failed", error="rejected")
    assert rq.confirm(db, BID, row["id"]) is not None           # retry
    assert len(rq.claim_confirmed(db, BID)) == 1
    rq.mark(db, row["id"], "posted", voucher_id="V2")
    assert db.rows[0]["status"] == "posted"


# ── parametrised matrix over states x amounts (many concrete cases) ───────────
@pytest.mark.parametrize("start_status", ["pending", "failed"])
@pytest.mark.parametrize("amount", [1, 100, 9260, 250000, 1_000_000])
def test_confirm_from_retryable_states(start_status, amount):
    db = FakeDB()
    row = _open(db, "c1", amount)
    db.rows[0]["status"] = start_status
    out = rq.confirm(db, BID, row["id"], amount=amount)
    assert out is not None and out["status"] == "confirmed"
    assert db.rows[0]["amount"] == float(amount)


@pytest.mark.parametrize("bad_status", ["skipped", "posted"])
def test_confirm_from_terminal_states(bad_status):
    db = FakeDB()
    row = _open(db); db.rows[0]["status"] = bad_status
    out = rq.confirm(db, BID, row["id"])
    if bad_status == "posted":
        assert out is not None                                  # already done = success
    else:
        assert out is None                                      # skipped stays skipped


# ── fuzz: long random op sequences never lose a payment or double-post ────────
@pytest.mark.parametrize("seed", range(120))
def test_fuzz_never_loses_or_double_posts(seed):
    rng = random.Random(seed)
    db = FakeDB()
    created_ids = set()
    posted_ids = set()
    clients = ["c%d" % i for i in range(4)]

    for _ in range(rng.randint(10, 40)):
        op = rng.choice(["create", "confirm", "claim", "post", "fail", "cancel"])
        if op == "create":
            r = _open(db, rng.choice(clients), rng.randint(1, 99999))
            if r:
                created_ids.add(r["id"])
        elif op == "confirm":
            cand = [r for r in db.rows if r["status"] in ("pending", "failed")]
            if cand:
                rq.confirm(db, BID, rng.choice(cand)["id"])
        elif op == "claim":
            claimed = rq.claim_confirmed(db, BID)
            # a claim must never hand out a row already posted
            for c in claimed:
                assert c["id"] not in posted_ids, "double-post: %s" % c["id"]
        elif op == "post":
            cand = [r for r in db.rows if r["status"] == "posting"]
            if cand:
                pid = rng.choice(cand)["id"]
                rq.mark(db, pid, "posted", voucher_id="V")
                posted_ids.add(pid)
        elif op == "fail":
            cand = [r for r in db.rows if r["status"] == "posting"]
            if cand:
                rq.mark(db, rng.choice(cand)["id"], "failed", error="x")
        elif op == "cancel":
            cand = [r for r in db.rows if r["status"] in ("pending", "failed")]
            if cand:
                rq.mark(db, rng.choice(cand)["id"], "skipped")

    # invariant 1: nothing created ever vanished - every id is still fetchable
    for cid in created_ids:
        assert rq.get_by_id(db, BID, cid) is not None, "lost payment %s" % cid
    # invariant 2: a posted row is terminal - it is never in a claimable state
    for r in db.rows:
        if r["id"] in posted_ids:
            assert r["status"] == "posted"
