"""SENT command: the owner asks the bot who got reminders today, by name.
The EOD digest gives only the count; this lists the parties, splitting
delivered from queued/failed so nothing is misreported as sent.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot


class _Msgs:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _Msgs(self._rows)


def _run(rows, monkeypatch):
    monkeypatch.setattr(bot, "require_db", lambda: FakeDB(rows))
    return asyncio.run(bot._handle_sent_today("biz1"))


def _row(name, status):
    return {"client_id": name, "delivery_status": status, "clients": {"name": name}}


def test_lists_delivered_parties_by_name(monkeypatch):
    out = _run([_row("Ramesh Traders", "sent"),
                _row("Suresh & Sons", "sent")], monkeypatch)
    assert "Reminders sent today (2)" in out
    assert "1. Ramesh Traders" in out
    assert "2. Suresh & Sons" in out


def test_collapses_repeat_sends_to_one_party(monkeypatch):
    out = _run([_row("Ramesh Traders", "sent"),
                _row("Ramesh Traders", "sent")], monkeypatch)
    assert "Reminders sent today (2)" in out
    assert "Ramesh Traders (x2)" in out       # one line, count shown


def test_queued_only_is_not_reported_as_sent(monkeypatch):
    out = _run([_row("Ramesh", "queued"), _row("Suresh", "queued")], monkeypatch)
    assert "sent" not in out.lower().split("\n")[0]
    assert "queue" in out.lower()
    assert "2 are in the queue" in out


def test_delivered_plus_queued_and_failed_footer(monkeypatch):
    out = _run([_row("Ramesh", "sent"),
                _row("Suresh", "queued"),
                _row("Mahesh", "failed")], monkeypatch)
    assert "Reminders sent today (1)" in out
    assert "1. Ramesh" in out
    assert "1 more waiting to send" in out
    assert "1 could not be sent" in out


def test_nothing_today(monkeypatch):
    out = _run([], monkeypatch)
    assert out == "No reminders have gone out today yet."


def test_command_routes_from_handle(monkeypatch):
    """SENT / REMINDED reach the handler for a recognised owner."""
    calls = []

    async def fake_handler(bid):
        calls.append(bid); return "OK-SENT"

    monkeypatch.setattr(bot, "_handle_sent_today", fake_handler)
    monkeypatch.setattr(bot, "_match_row",
                        lambda db, table, sel, num: {"id": "biz1", "business_name": "R",
                                                     "plan": "growth"} if table == "businesses" else None)
    monkeypatch.setattr(bot, "require_db", lambda: object())
    for cmd in ("SENT", "sent today", "reminded"):
        calls.clear()
        r = asyncio.run(bot.handle("919444294894", cmd, channel="bot"))
        assert r == "OK-SENT" and calls == ["biz1"], cmd
