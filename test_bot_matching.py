"""End-to-end proof that the forgiving name matcher + bilingual replies are wired
into the owner commands (via EXCLUDE, which is the simplest name-taking command).

Covers: a partial/prefixed/messy Tally name resolves to the right party; an
ambiguous name asks which one (never guesses); and a Hinglish owner is answered
in Hinglish.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot, checkpoint


class _Rec:
    def __init__(self):
        self.updates = []


class _T:
    def __init__(self, rec, clients):
        self.rec = rec; self.clients = clients; self._op = None; self._p = None
    def update(self, p): self._op = "update"; self._p = dict(p); return self
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def limit(self, n): return self
    def execute(self):
        if self._op == "update":
            self.rec.updates.append(self._p)
            return type("R", (), {"data": []})()
        return type("R", (), {"data": [dict(c) for c in self.clients]})()


class _DB:
    def __init__(self, rec, clients):
        self.rec = rec; self.clients = clients
    def table(self, name):
        return _T(self.rec, self.clients)


def _owner(monkeypatch, rec, clients, lang="english"):
    monkeypatch.setattr(bot, "require_db", lambda: _DB(rec, clients))
    monkeypatch.setattr(bot, "_match_row", lambda db, t, s, n: (
        {"id": "biz1", "business_name": "R", "plan": "pro", "owner_language": lang}
        if t == "businesses" else None))
    monkeypatch.setattr(checkpoint, "get_today", lambda db, bid: None)


def test_partial_messy_name_resolves_the_right_party(monkeypatch):
    rec = _Rec()
    _owner(monkeypatch, rec, [
        {"id": "c1", "name": "M/S RAMESH TRADERS-RTE4"},
        {"id": "c2", "name": "Suresh Textiles"}])
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE ramesh tr", channel="bot"))
    assert rec.updates == [{"excluded": True}]        # matched despite M/S + route tag
    assert "Ramesh Traders" in out                    # shown as the clean name


def test_ambiguous_name_asks_which_one(monkeypatch):
    rec = _Rec()
    _owner(monkeypatch, rec, [
        {"id": "c1", "name": "Ramesh Traders"},
        {"id": "c2", "name": "Ramesh Electricals"}])
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE ramesh", channel="bot"))
    assert rec.updates == []                           # never guessed
    assert "more than one" in out.lower()
    assert "Ramesh Traders" in out and "Ramesh Electricals" in out


def test_hinglish_owner_is_answered_in_hinglish(monkeypatch):
    rec = _Rec()
    _owner(monkeypatch, rec, [{"id": "c1", "name": "Ramesh Traders"}], lang="hinglish")
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE Ramesh", channel="bot"))
    assert rec.updates == [{"excluded": True}]
    assert "list par hai" in out                       # the Hinglish excluded_on copy


def test_english_owner_is_answered_in_english(monkeypatch):
    rec = _Rec()
    _owner(monkeypatch, rec, [{"id": "c1", "name": "Ramesh Traders"}], lang="english")
    out = asyncio.run(bot.handle("919444294894", "EXCLUDE Ramesh", channel="bot"))
    assert "do-not-chase" in out and "par hai" not in out
