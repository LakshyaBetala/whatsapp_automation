"""End-to-end smoke tests for the WhatsApp bot + reminder batch model.

These exercise the real routing/handlers against a small in-memory fake DB, so
regressions in the owner-only bot channel, non-Tally bill creation, and the
simplified batch model (language + UPI + time, no tone) are caught by `pytest`.
"""
import asyncio

import pytest

from app.services import batches as B


# ── Batch model (no DB) ──────────────────────────────────────────────────
def test_batch_schema_is_language_upi_time_disc_only():
    b = B.normalize_batch({"name": "VIP", "lang": "english", "upi": "x@ok",
                           "disc": 2, "hour": 9, "style": "firm", "line": "old"})
    assert set(b) == {"name", "lang", "upi", "disc", "hour"}   # no style / line
    assert b["lang"] == "english" and b["hour"] == 9 and b["upi"] == "x@ok"


def test_batch_hour_falls_back_to_business_default():
    biz = {"reminder_hour": 10}
    assert B.batch_hour(biz, {"hour": 8}) == 8          # batch's own hour
    assert B.batch_hour(biz, {}) == 10                   # falls back to business
    assert B.batch_hour({}, {}) == 11                    # global default


def test_batch_vpa_prefers_batch_then_shop_default():
    biz = {"upi_vpa": "shop@ok"}
    assert B.batch_vpa(biz, {"upi": "vip@ok"}) == "vip@ok"
    assert B.batch_vpa(biz, {"upi": ""}) == "shop@ok"


def test_get_batches_defaults_when_none_configured():
    got = B.get_batches({"msg_language": "english", "reminder_hour": 9})
    assert len(got) == 1 and got[0]["lang"] == "english" and got[0]["hour"] == 9


# ── Fake DB good enough for bot.handle routing ───────────────────────────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, sink=None):
        self._rows = rows
        self._sink = sink          # for insert capture
        self._insert = None

    # chainable no-ops (filters are ignored; presets drive the result)
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def like(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, row):
        self._insert = row
        return self

    def update(self, row):
        self._insert = row
        return self

    def execute(self):
        if self._insert is not None:
            saved = dict(self._insert)
            saved.setdefault("id", "new-id")
            if self._sink is not None:
                self._sink.append(saved)
            return _Result([saved])
        return _Result(self._rows)


class FakeDB:
    def __init__(self, tables):
        self.tables = tables       # name -> list[row]
        self.inserts = {}          # name -> list[captured]

    def table(self, name):
        self.inserts.setdefault(name, [])
        return _Query(self.tables.get(name, []), sink=self.inserts[name])


def _run(coro):
    return asyncio.run(coro)


OWNER = "919444294894"
BIZ = {"id": "biz1", "business_name": "RISHAB TRADING", "plan": "pro",
       "whatsapp_number": OWNER, "upi_vpa": "rtc@ok", "discount_pct": 0,
       "msg_language": "hinglish"}


def _patch(monkeypatch, tables):
    from app.services import bot
    fake = FakeDB(tables)
    monkeypatch.setattr(bot, "require_db", lambda: fake)
    return fake


def test_bot_channel_is_owner_only_for_strangers(monkeypatch):
    """A non-owner on the BOT number gets the owner-only line, never customer
    self-service."""
    _patch(monkeypatch, {"businesses": [], "clients": []})
    from app.services import bot
    reply = _run(bot.handle("910000000000", "HI", channel="bot"))
    assert "registered" in reply.lower()          # "registered owners only"
    # and stays silent on non-greetings
    reply2 = _run(bot.handle("910000000000", "random text", channel="bot"))
    assert reply2 == ""


def test_owner_gets_menu_on_bot_channel(monkeypatch):
    _patch(monkeypatch, {"businesses": [BIZ], "clients": []})
    from app.services import bot
    reply = _run(bot.handle(OWNER, "HELP", channel="bot"))
    assert "LIST" in reply and "BILL" in reply       # owner command menu


def test_owner_text_bill_creates_bill_and_client(monkeypatch):
    fake = _patch(monkeypatch, {"businesses": [BIZ], "clients": [], "bills": []})
    from app.services import bot
    reply = _run(bot.handle(OWNER, "BILL Naya Traders 5000", channel="bot"))
    assert "5,000" in reply or "5000" in reply
    # a client and a bill were inserted
    assert fake.inserts["clients"], "client should be created"
    assert fake.inserts["bills"], "bill should be created"
    # 'manual' is the only typed-bill value bills_source_check allows
    # (tally/photo/manual - 'text' violates the live constraint).
    assert fake.inserts["bills"][0]["source"] == "manual"


def test_shop_channel_stranger_greeting_gets_customer_help(monkeypatch):
    """On the SHOP number a stranger greeting still gets the customer help (not
    the owner-only line) - the two channels stay distinct."""
    _patch(monkeypatch, {"businesses": [], "clients": []})
    from app.services import bot
    reply = _run(bot.handle("910000000000", "HI", channel="shop"))
    assert "HISAB" in reply.upper()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
