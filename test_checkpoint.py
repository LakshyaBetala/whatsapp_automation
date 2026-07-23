"""Morning pre-reminder checkpoint (Option A: hold + nudge Tally, never mark paid).

Covers the state service (set/get/hold/hold_all/held_sets) and the owner's bot
replies (PAID n, PAID name, OK, HOLD), including that a PAID naming no listed
party falls through to the normal PAID.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot, checkpoint


# ── Fake DB backing the businesses table (for the state service) ──────────
class _BQ:
    def __init__(self, rows):
        self.rows = rows
        self._op = None
        self._patch = None
        self._filters = []

    def update(self, patch):
        self._op = "update"; self._patch = dict(patch); return self

    def select(self, *a, **k):
        self._op = "select"; return self

    def eq(self, f, v):
        self._filters.append(("eq", f, v)); return self

    def in_(self, f, v):
        self._filters.append(("in", f, v)); return self

    def limit(self, n):
        return self

    def _match(self, r):
        for op, f, v in self._filters:
            if op == "eq" and r.get(f) != v:
                return False
            if op == "in" and r.get(f) not in v:
                return False
        return True

    def execute(self):
        R = lambda d: type("R", (), {"data": d})()
        hit = [r for r in self.rows if self._match(r)]
        if self._op == "update":
            for r in hit:
                r.update(self._patch)
            return R(hit)
        return R([dict(r) for r in hit])


class FakeDB:
    def __init__(self, businesses):
        self.businesses = businesses

    def table(self, name):
        return _BQ(self.businesses)


ITEMS = [{"id": "c1", "name": "Ramesh Traders", "amount": 40000, "days": 60},
         {"id": "c2", "name": "Suresh & Sons", "amount": 25000, "days": 48}]


# ── State service ─────────────────────────────────────────────────────────
def test_set_and_get_today():
    db = FakeDB([{"id": "b1"}])
    assert checkpoint.set_today(db, "b1", ITEMS) is True
    cur = checkpoint.get_today(db, "b1")
    assert cur and cur["items"] == ITEMS and cur["held"] == []


def test_get_today_none_without_a_checkpoint():
    db = FakeDB([{"id": "b1"}])
    assert checkpoint.get_today(db, "b1") is None


def test_hold_one_party():
    db = FakeDB([{"id": "b1"}])
    checkpoint.set_today(db, "b1", ITEMS)
    assert checkpoint.hold(db, "b1", "c1") is True
    assert checkpoint.get_today(db, "b1")["held"] == ["c1"]


def test_hold_all_parties():
    db = FakeDB([{"id": "b1"}])
    checkpoint.set_today(db, "b1", ITEMS)
    assert checkpoint.hold_all(db, "b1") == 2
    assert set(checkpoint.get_today(db, "b1")["held"]) == {"c1", "c2"}


def test_held_sets_only_todays_business():
    db = FakeDB([{"id": "b1"}, {"id": "b2"}])
    checkpoint.set_today(db, "b1", ITEMS)
    checkpoint.hold(db, "b1", "c1")
    hs = checkpoint.held_sets(db, ["b1", "b2"])
    assert hs.get("b1") == {"c1"} and "b2" not in hs


def test_missing_columns_degrade_gracefully(monkeypatch):
    class _Boom:
        def table(self, *a, **k): raise RuntimeError("column checkpoint_date does not exist")
    assert checkpoint.get_today(_Boom(), "b1") is None
    assert checkpoint.held_sets(_Boom(), ["b1"]) == {}


# ── Owner bot replies ─────────────────────────────────────────────────────
class _Permissive:
    """A db that tolerates any query chain and returns empty, so handle()'s
    pre-command lookups (photo bills etc.) never blow up in these tests."""
    def __getattr__(self, _): return self
    def __call__(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": []})()


def _owner(monkeypatch, cp):
    monkeypatch.setattr(bot, "require_db", lambda: _Permissive())
    monkeypatch.setattr(bot, "_match_row",
                        lambda db, table, sel, num: (
                            {"id": "biz1", "business_name": "R", "plan": "pro"}
                            if table == "businesses" else None))
    monkeypatch.setattr(checkpoint, "get_today", lambda db, bid: cp)


def test_checkpoint_paid_by_number_holds(monkeypatch):
    holds = []
    _owner(monkeypatch, {"items": ITEMS, "held": []})
    monkeypatch.setattr(checkpoint, "hold", lambda db, bid, cid: holds.append(cid) or True)
    out = asyncio.run(bot.handle("919444294894", "PAID 1", channel="bot"))
    assert "Held Ramesh Traders" in out and "Tally" in out
    assert holds == ["c1"]


def test_checkpoint_paid_by_name_holds(monkeypatch):
    holds = []
    _owner(monkeypatch, {"items": ITEMS, "held": []})
    monkeypatch.setattr(checkpoint, "hold", lambda db, bid, cid: holds.append(cid) or True)
    out = asyncio.run(bot.handle("919444294894", "PAID suresh", channel="bot"))
    assert "Held Suresh & Sons" in out
    assert holds == ["c2"]


def test_checkpoint_ok_sends_all(monkeypatch):
    _owner(monkeypatch, {"items": ITEMS, "held": []})
    out = asyncio.run(bot.handle("919444294894", "OK", channel="bot"))
    assert "send today's reminders" in out.lower()


def test_checkpoint_hold_all(monkeypatch):
    _owner(monkeypatch, {"items": ITEMS, "held": []})
    monkeypatch.setattr(checkpoint, "hold_all", lambda db, bid: 2)
    out = asyncio.run(bot.handle("919444294894", "HOLD", channel="bot"))
    assert "Held all 2" in out


def test_paid_unlisted_party_falls_through_to_normal_paid(monkeypatch):
    _owner(monkeypatch, {"items": ITEMS, "held": []})
    called = []

    async def fake_paid(bid, name, plan):
        called.append(name); return "NORMAL-PAID"

    monkeypatch.setattr(bot, "_handle_paid_owner", fake_paid)
    out = asyncio.run(bot.handle("919444294894", "PAID Mahesh", channel="bot"))
    # The normal PAID handler matches case-insensitively, so it forwards the
    # upper-cased name - the point is it reached the normal handler at all.
    assert out == "NORMAL-PAID" and called == ["MAHESH"]


def test_paid_with_no_active_checkpoint_is_normal(monkeypatch):
    _owner(monkeypatch, None)         # get_today -> None
    called = []

    async def fake_paid(bid, name, plan):
        called.append(name); return "NORMAL-PAID"

    monkeypatch.setattr(bot, "_handle_paid_owner", fake_paid)
    out = asyncio.run(bot.handle("919444294894", "PAID Ramesh", channel="bot"))
    assert out == "NORMAL-PAID"
