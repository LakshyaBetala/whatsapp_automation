"""Inbound WhatsApp webhook (app/routers/webhooks.py).

The router's own docstring calls out two invariants that make this code
delicate: the POST endpoint must ALWAYS return 200 (a non-200 makes the BSP
retry, double-processing the command), and it must dedup by messageId (so a
redelivered message never replays a PAID/BILL command). Neither invariant had
a test before this file.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from fastapi.testclient import TestClient

from app.main import app
from app.routers import webhooks

client = TestClient(app)


# ── a tiny fake Supabase, just enough for the dedup select + insert ────────
class _Q:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._filters = []

    def select(self, *a, **k):
        return self

    def eq(self, f, v):
        self._filters.append((f, v))
        return self

    def limit(self, n):
        return self

    def insert(self, row):
        self.store.setdefault(self.name, []).append(dict(row))
        return self

    def execute(self):
        rows = self.store.get(self.name, [])
        for f, v in self._filters:
            rows = [r for r in rows if r.get(f) == v]
        return type("R", (), {"data": rows})()


class FakeDB:
    def __init__(self, tables=None):
        self.store = {k: list(v) for k, v in (tables or {}).items()}

    def table(self, name):
        return _Q(self.store, name)


BIZ = {"id": "biz1", "business_name": "TEST CO"}


# ── GET: Meta verification handshake ───────────────────────────────────────

def test_get_verify_returns_challenge_on_matching_token(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_verify_token", "secret-token")
    r = client.get("/webhooks/aisensy", params={
        "hub.mode": "subscribe", "hub.verify_token": "secret-token", "hub.challenge": "xyz123",
    })
    assert r.status_code == 200
    assert r.text == "xyz123"


def test_get_verify_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_verify_token", "secret-token")
    r = client.get("/webhooks/aisensy", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "xyz123",
    })
    assert r.status_code == 403


# ── POST: always 200 ─────────────────────────────────────────────────────

def test_post_ignores_payload_with_no_actionable_content(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "")
    r = client.post("/webhooks/aisensy", json={"data": {"sender": "919876543210"}})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ignored": True}


def test_post_wrong_secret_still_returns_200_and_does_not_process(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "shh")
    handle = AsyncMock(return_value="")
    with patch("app.services.bot.handle", handle):
        r = client.post(
            "/webhooks/aisensy",
            json={"data": {"sender": "919876543210", "message": "LIST"}},
            headers={"x-webhook-secret": "not-shh"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    handle.assert_not_called()


def test_post_internal_error_still_returns_200(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "")
    monkeypatch.setattr(webhooks, "require_db", lambda: FakeDB())
    handle = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.services.bot.handle", handle):
        r = client.post("/webhooks/aisensy", json={
            "data": {"sender": "919876543210", "message": "LIST", "messageId": "m1"},
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": "internal"}


# ── Security: bot-channel impersonation gate ─────────────────────────────

def test_bot_channel_without_secret_is_rejected_when_secret_configured(monkeypatch):
    """A stranger POSTing channel=bot (owner commands) must NOT be processed once a
    webhook secret is configured. This is the impersonation hole the secret closes."""
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "shh")
    handle = AsyncMock(return_value="SECRET DEBTOR LIST")
    with patch("app.services.bot.handle", handle):
        r = client.post("/webhooks/aisensy", json={
            "data": {"sender": "919876543210", "message": "LIST", "messageId": "z1", "channel": "bot"},
        })  # no x-webhook-secret header
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    handle.assert_not_called()          # owner handler never ran, no data leaked


def test_bot_channel_with_correct_secret_is_processed(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "shh")
    db = FakeDB({"businesses": [BIZ], "messages": []})
    monkeypatch.setattr(webhooks, "require_db", lambda: db)
    monkeypatch.setattr(webhooks, "_match_row", lambda db, table, field, val: BIZ if table == "businesses" else None)
    handle = AsyncMock(return_value="ok")
    with patch("app.services.bot.handle", handle):
        r = client.post("/webhooks/aisensy",
                        json={"data": {"sender": "919876543210", "message": "LIST",
                                       "messageId": "z2", "channel": "bot"}},
                        headers={"x-webhook-secret": "shh"})
    assert r.status_code == 200
    handle.assert_awaited_once()


# ── POST: dedup by messageId ─────────────────────────────────────────────

def test_post_processes_new_message_and_records_it(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "")
    db = FakeDB({"businesses": [BIZ], "messages": []})
    monkeypatch.setattr(webhooks, "require_db", lambda: db)
    monkeypatch.setattr(webhooks, "_match_row", lambda db, table, field, val: BIZ if table == "businesses" else None)
    handle = AsyncMock(return_value="reply text")
    with patch("app.services.bot.handle", handle):
        r = client.post("/webhooks/aisensy", json={
            "data": {"sender": "919876543210", "message": "LIST", "messageId": "m1", "channel": "bot"},
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "reply": "reply text"}
    handle.assert_awaited_once_with(
        "919876543210", "LIST", media_b64=None, media_type="image/jpeg", channel="bot",
    )
    # bookkeeping row was written so a redelivery of the same messageId dedups
    assert any(m.get("aisensy_message_id") == "m1" for m in db.store["messages"])


def test_post_duplicate_messageid_is_skipped_without_reprocessing(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "aisensy_webhook_secret", "")
    db = FakeDB({"businesses": [BIZ], "messages": [
        {"aisensy_message_id": "m1", "business_id": "biz1"},
    ]})
    monkeypatch.setattr(webhooks, "require_db", lambda: db)
    handle = AsyncMock(return_value="")
    with patch("app.services.bot.handle", handle):
        r = client.post("/webhooks/aisensy", json={
            "data": {"sender": "919876543210", "message": "LIST", "messageId": "m1"},
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "duplicate": True}
    handle.assert_not_called()


# ── payload shape extraction ─────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ({"data": {"sender": "919876543210", "message": "hi", "messageId": "a"}}, ("919876543210", "hi", "a")),
    ({"from": "919876543210", "text": "hi", "message_id": "b"}, ("919876543210", "hi", "b")),
    ({"data": {"mobile": "919876543210", "messageData": {"text": "hi"}}}, ("919876543210", "hi", None)),
    ({"data": {"waId": "919876543210", "text": {"body": "hi"}}}, ("919876543210", "hi", None)),
])
def test_extract_handles_known_payload_shapes(body, expected):
    assert webhooks._extract(body) == expected
