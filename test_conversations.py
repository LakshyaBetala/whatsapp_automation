"""Conversation memory (app/services/conversations.py) + the replies wiring.

Every inbound customer reply must be remembered (best-effort) so the tracker has
its story and the payment-behaviour dataset accrues. Storage failures must never
break the reply flow.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import conversations


# ── a tiny fake table ────────────────────────────────────────────────────────
class _Q:
    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows
        self._f = []
    def insert(self, row):
        self.sink.append(row)
        return self
    def select(self, *a, **k): return self
    def eq(self, f, v): self._f.append((f, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._n = n; return self
    def execute(self):
        out = [r for r in self.rows if all(r.get(f) == v for f, v in self._f)]
        return type("R", (), {"data": out[:getattr(self, "_n", None)]})()


class FakeDB:
    def __init__(self, rows=None):
        self.inserted = []
        self.rows = rows or []
    def table(self, name):
        return _Q(self.inserted, self.rows)


class BoomDB:
    def table(self, name):
        raise RuntimeError("db down")


# ── record ───────────────────────────────────────────────────────────────────
def test_record_stores_the_message():
    db = FakeDB()
    conversations.record(db, "b1", "c1", "paisa bhej diya", intent="paid_claim", from_number="9198")
    assert db.inserted == [{
        "business_id": "b1", "client_id": "c1", "from_number": "9198",
        "body": "paisa bhej diya", "intent": "paid_claim"}]


def test_record_skips_empty_and_missing_biz():
    db = FakeDB()
    conversations.record(db, "b1", "c1", "   ")
    conversations.record(db, "", "c1", "hi")
    assert db.inserted == []


def test_record_truncates_long_body():
    db = FakeDB()
    conversations.record(db, "b1", "c1", "x" * 5000)
    assert len(db.inserted[0]["body"]) == 1000


def test_record_never_raises_on_db_error():
    conversations.record(BoomDB(), "b1", "c1", "hello")   # must not raise


def test_record_allows_null_client():
    db = FakeDB()
    conversations.record(db, "b1", None, "from an unknown number")
    assert db.inserted[0]["client_id"] is None


# ── recent_for_client ────────────────────────────────────────────────────────
def test_recent_returns_party_messages():
    rows = [
        {"business_id": "b1", "client_id": "c1", "body": "one", "intent": None, "created_at": "t1"},
        {"business_id": "b1", "client_id": "c2", "body": "other", "intent": None, "created_at": "t2"},
    ]
    db = FakeDB(rows)
    out = conversations.recent_for_client(db, "b1", "c1")
    assert len(out) == 1 and out[0]["body"] == "one"


def test_recent_handles_errors_and_blanks():
    assert conversations.recent_for_client(BoomDB(), "b1", "c1") == []
    assert conversations.recent_for_client(FakeDB(), "b1", "") == []


# ── the replies wiring: a reply gets remembered ──────────────────────────────
def test_capture_reply_records_the_inbound(monkeypatch):
    from app.services import replies
    db = FakeDB()
    monkeypatch.setattr(replies, "require_db", lambda: db)

    async def _classify(t):
        return {"intent": "chatter", "amount": None, "promise_date": None, "confidence": 0.9}
    monkeypatch.setattr(replies.intent, "classify", _classify)

    client = {"id": "c1", "name": "Ramesh", "business_id": "b1"}
    asyncio.run(replies.capture_reply(client, "kal baat karte hain"))
    assert db.inserted and db.inserted[0]["body"] == "kal baat karte hain"
    assert db.inserted[0]["business_id"] == "b1" and db.inserted[0]["client_id"] == "c1"


def test_capture_reply_records_screenshot(monkeypatch):
    from app.services import replies
    db = FakeDB()
    monkeypatch.setattr(replies, "require_db", lambda: db)
    monkeypatch.setattr(replies.promises, "create", lambda *a, **k: {"id": "p1"})

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(replies, "_forward_proof", _noop)
    monkeypatch.setattr(replies.whatsapp, "notify_owner", _noop)

    client = {"id": "c1", "name": "Ramesh", "business_id": "b1"}
    asyncio.run(replies.capture_reply(client, "", media_b64="ZmFrZQ=="))
    assert any(r["intent"] == "screenshot" for r in db.inserted)
