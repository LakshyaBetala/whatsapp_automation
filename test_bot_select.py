"""Which-one selection flow: an ambiguous name shows a NUMBERED list, and the
owner replies with a number to act on that exact party (by id) - so two shops
with similar names are never confused, and the owner never has to re-type."""
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot, checkpoint, promises


class _R:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, store, table): self.store = store; self.t = table
    def update(self, p): self.op = "update"; self.patch = dict(p); return self
    def select(self, *a, **k): self.op = "select"; return self
    def eq(self, f, v): self.f.setdefault(f, v); return self
    def in_(self, f, v): self.f[f] = ("in", list(v)); return self
    def ilike(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    op = "select"
    def __getattr__(self, n):  # tolerate any other builder call
        return lambda *a, **k: self
    def _match(self, r):
        for f, v in self.f.items():
            if isinstance(v, tuple) and v[0] == "in":
                if r.get(f) not in v[1]: return False
            elif r.get(f) != v: return False
        return True
    def execute(self):
        rows = self.store.rows.get(self.t, [])
        if getattr(self, "op", "select") == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.patch)
            self.store.updates.append((self.t, self.patch, [r.get("id") for r in hit]))
            return _R([dict(r) for r in hit])
        return _R([dict(r) for r in rows if self._match(r)])
    f = {}


class _Store:
    def __init__(self, clients):
        self.rows = {"clients": clients}
        self.updates = []
    def table(self, name):
        q = _Q(self, name); q.f = {}; q.op = "select"; return q


def _wire(monkeypatch, clients, lang="english"):
    store = _Store(clients)
    monkeypatch.setattr(bot, "require_db", lambda: store)
    monkeypatch.setattr(bot, "_match_row", lambda db, t, s, n: (
        {"id": "biz1", "business_name": "R", "plan": "pro", "owner_language": lang}
        if t == "businesses" else None))
    monkeypatch.setattr(checkpoint, "get_today", lambda db, bid: None)
    monkeypatch.setattr(promises, "close_for_client", lambda *a, **k: True)
    bot._PENDING_PICK.clear()
    return store


TWO = [{"id": "c1", "business_id": "biz1", "name": "Bhavani Electrical",
        "excluded": False, "reminders_enabled": True},
       {"id": "c2", "business_id": "biz1", "name": "Bhavani Electricals Chrompet",
        "excluded": False, "reminders_enabled": True}]


def test_ambiguous_sets_a_numbered_pick(monkeypatch):
    _wire(monkeypatch, TWO)
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE bhavani electrical", channel="bot"))
    assert "1." in out and "2." in out and "number" in out.lower()
    pick = bot._get_pick("biz1")
    assert pick and pick["verb"] == "EXCLUDE" and len(pick["cands"]) == 2


def test_reply_number_acts_on_that_exact_party(monkeypatch):
    store = _wire(monkeypatch, TWO)
    asyncio.run(bot.handle("919444294894", "EXCLUDE bhavani electrical", channel="bot"))
    out = asyncio.run(bot.handle("919444294894", "2", channel="bot"))
    # c2 (Chrompet) excluded - the SECOND candidate, never c1
    assert store.updates and store.updates[-1][2] == ["c2"]
    assert store.updates[-1][1] == {"excluded": True}
    assert bot._get_pick("biz1") is None                 # pick consumed


def test_reply_zero_cancels(monkeypatch):
    store = _wire(monkeypatch, TWO)
    asyncio.run(bot.handle("919444294894", "STOP bhavani electrical", channel="bot"))
    out = asyncio.run(bot.handle("919444294894", "0", channel="bot"))
    assert "cancel" in out.lower()
    assert store.updates == []                            # nothing changed
    assert bot._get_pick("biz1") is None


def test_paid_pick_keeps_the_amount(monkeypatch):
    _wire(monkeypatch, TWO)
    asyncio.run(bot.handle("919444294894", "PAID bhavani electrical 5000", channel="bot"))
    pick = bot._get_pick("biz1")
    assert pick and pick["verb"] == "PAID" and pick["suffix"].strip() == "5000"


# ── command typo suggestions ─────────────────────────────────────────────────
def test_suggest_command_on_typo():
    assert "PAID" in bot._suggest_command("pai ramesh", "english")
    assert "CHECK" in bot._suggest_command("chek ramesh", "english")
    assert bot._suggest_command("PAID ramesh", "english") == ""   # spelled right


def test_parse_selection():
    assert bot._parse_selection("2", 3) == 1
    assert bot._parse_selection("0", 3) == "cancel"
    assert bot._parse_selection("cancel", 3) == "cancel"
    assert bot._parse_selection("9", 3) is None                   # out of range
    assert bot._parse_selection("ramesh traders", 3) is None       # a name, not a pick
