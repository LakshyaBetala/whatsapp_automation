"""Smart onboarding nudges: welcome on first sync + chase the unsynced.

Covers assistant.welcome_owner_if_new (once, dedup, needs a number) and
assistant.nudge_unsynced (only recent paired-but-empty shops, once each).
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import assistant


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    """A tiny chainable stand-in for the supabase query builder."""
    def __init__(self, table, store):
        self.table, self.store = table, store
        self._eq = {}
        self._count = None

    def select(self, *a, **k):
        if k.get("count") == "exact":
            self._count = "exact"
        return self

    def eq(self, c, v): self._eq[c] = v; return self
    # filters we don't need to enforce for these tests -> no-ops
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def is_(self, *a, **k): return self

    @property
    def not_(self):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def execute(self):
        rows = [r for r in self.store.get(self.table, [])
                if all(r.get(c) == v for c, v in self._eq.items())]
        if getattr(self, "_patch", None) is not None:
            for r in rows:
                r.update(self._patch)
            return _Result(rows)
        return _Result(rows, count=(len(rows) if self._count else None))


class _DB:
    def __init__(self, tables):
        self.store = tables

    def table(self, name):
        return _Query(name, self.store)


def test_welcome_sent_once_then_deduped():
    db = _DB({"businesses": [
        {"id": "b1", "whatsapp_number": "9199", "welcomed_at": None,
         "owner_language": "english"}]})
    with patch("app.services.whatsapp.notify_owner", new_callable=AsyncMock) as no:
        first = asyncio.run(assistant.welcome_owner_if_new(db, "b1"))
        second = asyncio.run(assistant.welcome_owner_if_new(db, "b1"))
    assert first is True and second is False           # welcomed exactly once
    assert no.await_count == 1
    assert db.store["businesses"][0]["welcomed_at"]    # stamped


def test_welcome_skipped_without_number():
    db = _DB({"businesses": [
        {"id": "b1", "whatsapp_number": None, "welcomed_at": None}]})
    with patch("app.services.whatsapp.notify_owner", new_callable=AsyncMock) as no:
        out = asyncio.run(assistant.welcome_owner_if_new(db, "b1"))
    assert out is False
    no.assert_not_called()


def test_nudge_unsynced_messages_empty_shop_and_stamps():
    db = _DB({
        "businesses": [{"id": "b1", "whatsapp_number": "9199",
                        "owner_language": "hinglish", "unsynced_nudge_at": None}],
        "clients": [],                                  # b1 has NO data -> unsynced
    })
    with patch("app.services.whatsapp.notify_owner", new_callable=AsyncMock) as no:
        n = asyncio.run(assistant.nudge_unsynced(db))
    assert n == 1
    assert no.await_count == 1
    assert db.store["businesses"][0]["unsynced_nudge_at"]   # stamped (once)


def test_nudge_skips_shop_that_has_data():
    db = _DB({
        "businesses": [{"id": "b1", "whatsapp_number": "9199",
                        "unsynced_nudge_at": None}],
        "clients": [{"id": "c1", "business_id": "b1"}],  # already synced
    })
    with patch("app.services.whatsapp.notify_owner", new_callable=AsyncMock) as no:
        n = asyncio.run(assistant.nudge_unsynced(db))
    assert n == 0
    no.assert_not_called()
    assert db.store["businesses"][0]["unsynced_nudge_at"] is None
