"""Do-not-chase (exclude) list: the owner's EXCLUDE / INCLUDE commands set
clients.excluded, which the sweep and the morning checkpoint both honour."""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot, checkpoint


class _Rec:
    def __init__(self):
        self.updates = []


class _FakeTable:
    def __init__(self, rec, clients):
        self.rec = rec
        self.clients = clients
        self._op = None
        self._patch = None

    def update(self, patch):
        self._op = "update"; self._patch = dict(patch); return self
    def select(self, *a, **k):
        self._op = "select"; return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def limit(self, n): return self

    def execute(self):
        if self._op == "update":
            self.rec.updates.append(self._patch)
            return type("R", (), {"data": []})()
        # a select on clients: the resolver reads the whole party list
        return type("R", (), {"data": [dict(c) for c in self.clients]})()


class _FakeDB:
    def __init__(self, rec, clients):
        self.rec = rec
        self.clients = clients
    def table(self, name):
        return _FakeTable(self.rec, self.clients)


CLIENTS = [{"id": "c1", "name": "Ramesh Traders"}]


def _owner(monkeypatch, rec, clients=CLIENTS):
    monkeypatch.setattr(bot, "require_db", lambda: _FakeDB(rec, clients))
    monkeypatch.setattr(bot, "_match_row",
                        lambda db, table, sel, num: (
                            {"id": "biz1", "business_name": "R", "plan": "pro"}
                            if table == "businesses" else None))
    monkeypatch.setattr(checkpoint, "get_today", lambda db, bid: None)


def test_exclude_sets_flag(monkeypatch):
    rec = _Rec(); _owner(monkeypatch, rec)
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE Ramesh", channel="bot"))
    assert rec.updates == [{"excluded": True}]
    assert "do-not-chase" in out and "INCLUDE" in out


def test_include_clears_flag(monkeypatch):
    rec = _Rec(); _owner(monkeypatch, rec)
    out = asyncio.run(bot.handle("919444294894", "INCLUDE Ramesh", channel="bot"))
    assert rec.updates == [{"excluded": False}]
    assert "back on" in out


def test_exclude_unknown_party(monkeypatch):
    rec = _Rec(); _owner(monkeypatch, rec)
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE Nobody", channel="bot"))
    assert rec.updates == [] and "No party matches" in out
