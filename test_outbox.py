"""Outbox queue tests: on the bot deployment (SEND_VIA_OUTBOX=true) every
customer-facing send is QUEUED for the shop number, never sent from the bot;
owner-facing (platform) sends still go out directly. The shop-side sweep
keeps transient failures queued (shop laptop off) instead of dropping them.
"""
import asyncio

import pytest


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink
        self._insert = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def single(self): return self

    def insert(self, row):
        self._insert = row
        return self

    def update(self, row):
        self._insert = dict(row, _update=True)
        return self

    def execute(self):
        if self._insert is not None:
            saved = dict(self._insert)
            saved.setdefault("id", "row-id")
            self._sink.append(saved)
            return _Result([saved])
        return _Result(self._rows)


class FakeDB:
    def __init__(self, tables=None, allowed=True):
        self.tables = tables or {}
        self.writes = {}      # table -> list of inserted/updated rows
        self.allowed = allowed

    def table(self, name):
        self.writes.setdefault(name, [])
        return _Query(self.tables.get(name, []), self.writes[name])

    def rpc(self, name, params):
        return _Query([{"allowed": self.allowed}], [])


def _run(coro):
    return asyncio.run(coro)


def test_shop_channel_send_is_queued_when_outbox_mode(monkeypatch):
    from app.services import whatsapp
    fake = FakeDB(tables={"businesses": [{"plan_expires_on": None}]})
    monkeypatch.setattr(whatsapp, "require_db", lambda: fake)
    monkeypatch.setattr(whatsapp.settings, "send_via_outbox", True)

    result = _run(whatsapp.send_message(
        business_id="biz1", to_number="919812345678",
        message_text="namaste", channel="shop"))

    assert result["queued"] is True
    assert result["sent"] is False
    assert result["delivery_status"] == "queued"
    # audit row queued + outbox row carrying the exact wa payload
    assert fake.writes["messages"][0]["delivery_status"] == "queued"
    ob = fake.writes["wa_outbox"][0]
    assert ob["payload"]["phone"] == "919812345678"
    assert ob["payload"]["message"] == "namaste"
    assert ob["message_db_id"] == "row-id"


def test_platform_channel_still_sends_directly(monkeypatch):
    """Owner-facing sends (digest/alerts) must NOT be queued - they belong to
    the bot number. With no wa_service running they fail, but directly."""
    from app.services import whatsapp
    fake = FakeDB(tables={"businesses": [{"plan_expires_on": None}]})
    monkeypatch.setattr(whatsapp, "require_db", lambda: fake)
    monkeypatch.setattr(whatsapp.settings, "send_via_outbox", True)
    # Deterministic dead endpoint: the dev machine may have a REAL wa_service
    # on 3001 (the desktop app), which would answer 503 instead of refusing.
    monkeypatch.setattr(whatsapp.settings, "openwa_url", "http://127.0.0.1:9")

    result = _run(whatsapp.send_message(
        business_id="biz1", to_number="919444294894",
        message_text="digest", channel="platform"))

    assert not result.get("queued")
    assert "wa_outbox" not in {k for k, v in fake.writes.items() if v}
    # direct attempt against a dead port -> classified, not silent
    assert result["sent"] is False
    assert result["reason"] in ("wa_service_down", "send_failed")


def test_outbox_sweep_keeps_queue_when_shop_wa_offline(monkeypatch):
    """Shop laptop off/WhatsApp down = transient: the row STAYS queued."""
    from app.jobs import outbox_sweep
    from datetime import datetime, timezone
    row = {"id": "ob1", "business_id": "biz1", "message_db_id": "m1",
           "payload": {"phone": "919812345678", "message": "hi"},
           "attempts": 0,
           "created_at": datetime.now(timezone.utc).isoformat()}
    fake = FakeDB(tables={"wa_outbox": [row]})
    monkeypatch.setattr(outbox_sweep, "require_db", lambda: fake)

    _run(outbox_sweep.run())

    upd = fake.writes["wa_outbox"][0]
    assert upd["status"] == "queued"       # NOT failed - retried next minute
    assert upd["attempts"] == 1
    # messages row untouched while still queued
    assert not fake.writes.get("messages")


def test_outbox_sweep_expires_stale_rows(monkeypatch):
    from app.jobs import outbox_sweep
    row = {"id": "ob2", "business_id": "biz1", "message_db_id": "m2",
           "payload": {"phone": "919812345678", "message": "hi"},
           "attempts": 0,
           "created_at": "2026-01-01T00:00:00+00:00"}   # months old
    fake = FakeDB(tables={"wa_outbox": [row]})
    monkeypatch.setattr(outbox_sweep, "require_db", lambda: fake)

    _run(outbox_sweep.run())

    upd = fake.writes["wa_outbox"][0]
    assert upd["status"] == "failed"
    assert upd["last_error"] == "expired"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
