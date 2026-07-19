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
    # Pin the window OPEN so this test is about retry policy, not the clock.
    monkeypatch.setattr(outbox_sweep.settings, "enforce_send_window", False)

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
    monkeypatch.setattr(outbox_sweep.settings, "enforce_send_window", False)

    _run(outbox_sweep.run())

    upd = fake.writes["wa_outbox"][0]
    assert upd["status"] == "failed"
    assert upd["last_error"] == "expired"


# ── Send window (quiet hours) ─────────────────────────────────────────────
def test_send_window_boundaries():
    """Queued CUSTOMER sends only leave during shop hours, so a laptop switched
    on at midnight can't blast the day's backlog."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.jobs import outbox_sweep
    IST = ZoneInfo("Asia/Kolkata")

    inside = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    assert outbox_sweep.within_send_window(inside) is True
    # start is inclusive, end is exclusive
    assert outbox_sweep.within_send_window(datetime(2026, 7, 20, 9, 0, tzinfo=IST)) is True
    assert outbox_sweep.within_send_window(datetime(2026, 7, 20, 18, 59, tzinfo=IST)) is True
    assert outbox_sweep.within_send_window(datetime(2026, 7, 20, 19, 0, tzinfo=IST)) is False
    assert outbox_sweep.within_send_window(datetime(2026, 7, 20, 8, 59, tzinfo=IST)) is False
    assert outbox_sweep.within_send_window(datetime(2026, 7, 20, 23, 30, tzinfo=IST)) is False


def test_send_window_can_be_disabled(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.jobs import outbox_sweep
    monkeypatch.setattr(outbox_sweep.settings, "enforce_send_window", False)
    midnight = datetime(2026, 7, 20, 0, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert outbox_sweep.within_send_window(midnight) is True


def test_sweep_sends_nothing_outside_the_window(monkeypatch):
    """Outside shop hours the queue is left completely untouched."""
    from datetime import datetime, timezone
    from app.jobs import outbox_sweep
    row = {"id": "ob3", "business_id": "biz1", "message_db_id": "m3",
           "payload": {"phone": "919812345678", "message": "hi"},
           "attempts": 0,
           "created_at": datetime.now(timezone.utc).isoformat()}
    fake = FakeDB(tables={"wa_outbox": [row]})
    monkeypatch.setattr(outbox_sweep, "require_db", lambda: fake)
    monkeypatch.setattr(outbox_sweep.settings, "enforce_send_window", True)
    # A window that can never contain "now" (start == end -> empty range).
    monkeypatch.setattr(outbox_sweep.settings, "send_window_start_hour", 0)
    monkeypatch.setattr(outbox_sweep.settings, "send_window_end_hour", 0)

    _run(outbox_sweep.run())

    assert not fake.writes.get("wa_outbox")   # nothing attempted, nothing marked


# ── Store-forward-delete ──────────────────────────────────────────────────
def test_cleanup_pdf_deletes_stored_invoice_after_delivery(monkeypatch):
    from app.jobs import outbox_sweep
    calls = []
    monkeypatch.setattr(outbox_sweep.pdf_service, "delete_pdf",
                        lambda bill_id, inv: calls.append((bill_id, inv)))
    fake = FakeDB(tables={"messages": [{"bill_id": "bill-9"}],
                          "bills": [{"invoice_number": "INV-7"}]})

    outbox_sweep._cleanup_pdf(fake, "m1")

    assert calls == [("bill-9", "INV-7")]


def test_cleanup_pdf_noop_when_message_has_no_bill(monkeypatch):
    from app.jobs import outbox_sweep
    calls = []
    monkeypatch.setattr(outbox_sweep.pdf_service, "delete_pdf",
                        lambda bill_id, inv: calls.append((bill_id, inv)))
    fake = FakeDB(tables={"messages": [{"bill_id": None}]})   # a reminder, not a bill

    outbox_sweep._cleanup_pdf(fake, "m1")

    assert calls == []


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
