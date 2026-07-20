"""Thin-client outbox drain over HTTP: a keyless shop pulls and acks its own
queued sends. The server keeps the queue, the send window, and the security
boundary (a token can only touch its own rows).
"""
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import outbox


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.rows = store, name, store[name]
        self._op = self._payload = self._limit = None
        self._filters = []

    def select(self, *a, **k): self._op = "select"; return self
    def update(self, patch): self._op, self._payload = "update", dict(patch); return self
    def eq(self, f, v): self._filters.append((f, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._limit = n; return self

    def _match(self, r): return all(r.get(f) == v for f, v in self._filters)

    def execute(self):
        R = lambda d: type("R", (), {"data": d})()
        if self._op == "update":
            hit = [r for r in self.rows if self._match(r)]
            for r in hit:
                r.update(self._payload)
            return R([dict(r) for r in hit])
        out = [dict(r) for r in self.rows if self._match(r)]
        if self._limit is not None:
            out = out[:self._limit]
        return R(out)


class FakeDB:
    def __init__(self, outbox_rows=None):
        self.store = {"wa_outbox": outbox_rows or [], "messages": [], "bills": []}

    def table(self, name):
        self.store.setdefault(name, [])
        return _Q(self.store, name)


def _row(rid, biz="biz1", status="queued", mins_old=1, msg="m1"):
    return {"id": rid, "business_id": biz, "status": status,
            "message_db_id": msg, "attempts": 0,
            "payload": {"phone": "919812345678", "message": "hi"},
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=mins_old)).isoformat()}


@pytest.fixture(autouse=True)
def _window_open(monkeypatch):
    # These tests are about queue logic, not the clock.
    monkeypatch.setattr(outbox, "within_send_window", lambda *a, **k: True)


# ── pull ──────────────────────────────────────────────────────────────────
def test_pull_returns_only_this_business_queued_rows():
    db = FakeDB([_row("a", biz="biz1"), _row("b", biz="biz2"),
                 _row("c", biz="biz1", status="sent")])
    items = outbox.pull(db, "biz1")
    assert [i["id"] for i in items] == ["a"]      # not biz2's, not the sent one


def test_pull_is_empty_outside_the_send_window(monkeypatch):
    monkeypatch.setattr(outbox, "within_send_window", lambda *a, **k: False)
    db = FakeDB([_row("a")])
    assert outbox.pull(db, "biz1") == []


def test_pull_expires_stale_rows_instead_of_delivering_them():
    db = FakeDB([_row("old", mins_old=60 * 80)])   # ~3.3 days old > 72h
    items = outbox.pull(db, "biz1")
    assert items == []
    assert db.store["wa_outbox"][0]["status"] == "failed"
    assert db.store["wa_outbox"][0]["last_error"] == "expired"


# ── ack ───────────────────────────────────────────────────────────────────
def test_ack_sent_marks_row_and_mirrors_audit(monkeypatch):
    cleaned = []
    monkeypatch.setattr(outbox, "_cleanup_pdf", lambda db, mid: cleaned.append(mid))
    db = FakeDB([_row("a", msg="msg-9")])
    db.store["messages"].append({"id": "msg-9", "delivery_status": "queued", "bill_id": "b1"})

    assert outbox.ack(db, "biz1", "a", "sent") is True
    assert db.store["wa_outbox"][0]["status"] == "sent"
    assert db.store["wa_outbox"][0]["sent_at"]
    assert db.store["messages"][0]["delivery_status"] == "sent"
    assert cleaned == ["msg-9"]        # store-forward-delete fired


def test_ack_cannot_touch_another_businesss_row():
    db = FakeDB([_row("a", biz="biz1")])
    # A token for biz2 must not be able to ack biz1's row.
    assert outbox.ack(db, "biz2", "a", "sent") is False
    assert db.store["wa_outbox"][0]["status"] == "queued"   # untouched


def test_ack_transient_keeps_it_queued(monkeypatch):
    monkeypatch.setattr(outbox, "_cleanup_pdf", lambda db, mid: None)
    db = FakeDB([_row("a")])
    outbox.ack(db, "biz1", "a", "queued", attempts=1, error="wa down")
    assert db.store["wa_outbox"][0]["status"] == "queued"
    assert db.store["wa_outbox"][0]["attempts"] == 1


# ── endpoint auth ─────────────────────────────────────────────────────────
def test_pull_endpoint_requires_a_real_token(monkeypatch):
    import asyncio
    from app.routers import license as lr
    from fastapi import HTTPException
    monkeypatch.setattr(lr, "require_db", lambda: FakeDB([]))
    with pytest.raises(HTTPException) as e:
        asyncio.run(lr.outbox_pull(lr.OutboxPullPayload(agent_token="nope")))
    assert e.value.status_code == 401
