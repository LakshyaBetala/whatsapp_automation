"""Non-owner -> smart join funnel (bot._prospect_reply + assistant.decide_prospect).

The ASVA marketing number IS the assistant/bot number, so a non-owner messaging
it (off the poster) is a lead, not a stranger. SMART funnel: pitch exactly ONCE
(English, with a YES call-to-action), then stay quiet so the bot never talks over
a live sales chat; a YES routes to the human follow-up. Never bounce a prospect
on the first touch; never use em/en dashes.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot


# ── Minimal in-memory stub for the leads / platform_config tables ─────────
class _Q:
    def __init__(self, store, table):
        self.store, self.table, self._eq = store, table, []

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self._eq.append((c, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, row, **k):
        key = row.get("from_number") or row.get("key")
        self.store.setdefault(self.table, {}).setdefault(key, {}).update(row)
        return self

    def update(self, patch):
        for r in self.store.get(self.table, {}).values():
            if all(r.get(c) == v for c, v in self._eq):
                r.update(patch)
        return self

    def execute(self):
        rows = list(self.store.get(self.table, {}).values())
        for c, v in self._eq:
            rows = [r for r in rows if r.get(c) == v]
        return type("R", (), {"data": rows})()


class _DB:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _Q(self.store, name)


def _reply(db, num, text):
    return bot._prospect_reply(db, num, text, text.upper().strip())


def test_first_inquiry_gets_english_invite_with_cta():
    # Each a fresh number so it is a first touch.
    for i, msg in enumerate(("hi", "hello", "kya hai ye?", "poster dekha")):
        r = _reply(_DB(), f"9190000000{i}", msg)
        assert "ASVA" in r
        assert "YES" in r                        # clear call to action
        assert "free" in r.lower() and "September" in r


def test_yes_on_first_touch_routes_to_followup():
    for i, word in enumerate(("YES", "haan", "Ha", "JOIN", "interested", "chahiye", "signup")):
        r = _reply(_DB(), f"9191111111{i}", word)
        assert "team" in r.lower() and "free" in r.lower(), word


def test_interested_within_a_sentence():
    r = _reply(_DB(), "919222222222", "yes please i want it")
    assert "team" in r.lower() and "free" in r.lower()


def test_pitches_only_once_then_stays_silent():
    db, num = _DB(), "919333333333"
    first = _reply(db, num, "what is this")
    assert "ASVA" in first                       # first touch pitches
    # Every later non-YES message stays silent - never talks over a real chat.
    assert _reply(db, num, "tell me more") == ""
    assert _reply(db, num, "hmm ok") == ""
    # ...but a YES still hands over.
    assert "team" in _reply(db, num, "YES").lower()


def test_global_switch_off_silences_everything():
    from app.services import assistant
    db = _DB()
    assistant.set_assistant_enabled(db, False)
    assert _reply(db, "919444444444", "hi") == ""


def test_english_only_no_hinglish():
    for i, msg in enumerate(("hi", "YES")):
        low = _reply(_DB(), f"9195555555{i}", msg).lower()
        for h in ("bhejein", "karta hai", "aapke", "humari", "bhasha"):
            assert h not in low, (msg, h)


def test_never_bounces_a_first_touch_prospect():
    for i, msg in enumerate(("YES", "kya hai", "random text", "hello")):
        assert "registered" not in _reply(_DB(), f"9196666666{i}", msg).lower()


def test_no_em_or_en_dashes():
    for i, msg in enumerate(("hello", "YES")):
        r = _reply(_DB(), f"9197777777{i}", msg)
        assert "—" not in r and "–" not in r
