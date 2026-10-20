"""Non-owner -> ASVA join funnel (bot._prospect_reply + assistant.decide_prospect).

The ASVA bot number is ALSO the founder's own WhatsApp, so most messages from
non-owners are personal chats, not leads. Rule: stay silent unless the person
clearly asks about ASVA ("I want to try ASVA", "ASVA demo", "ASVA price"...).
Then pitch once (14 days free from the day they start, YES to start); a YES after the
pitch hands over to the team; everything else stays silent. A bare "yes" from
someone who was never pitched is just a chat and gets nothing. No em/en dashes.
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


def test_personal_chats_get_no_automated_reply():
    for i, msg in enumerate(("hi", "hello", "kya hai ye?", "random text", "yes", "ok",
                             "bhai kal milte hai", "call me", "sent the photos")):
        assert _reply(_DB(), f"9190000000{i}", msg) == "", msg


def test_asking_for_asva_starts_the_funnel():
    for i, msg in enumerate(("I want to try ASVA", "asva demo chahiye", "ASVA price?",
                             "Hi ASVA", "how does asva work", "asva kaise use kare",
                             "interested in ASVA for my shop")):
        r = _reply(_DB(), f"9191111111{i}", msg)
        assert "ASVA" in r and "YES" in r, msg
        assert "14 days" in r, msg


def test_mentioning_asva_without_wanting_it_stays_silent():
    assert _reply(_DB(), "919222222220", "my nephew Asva has his exam tomorrow morning") == ""


def test_yes_after_the_pitch_hands_over():
    db, num = _DB(), "919333333333"
    assert "YES" in _reply(db, num, "I want to try ASVA")
    for word in ("hmm ok", "tell me later"):
        assert _reply(db, num, word) == ""          # never talks over a real chat
    r = _reply(db, num, "YES")
    assert "team" in r.lower() and "14 days" in r
    assert _reply(db, num, "thanks") == ""          # a human is on it now


def test_yes_words_hand_over_only_after_a_pitch():
    for i, word in enumerate(("YES", "haan", "Ha", "JOIN", "interested", "chahiye", "signup")):
        assert _reply(_DB(), f"9194444444{i}", word) == "", word
        db, num = _DB(), f"9194444445{i}"
        _reply(db, num, "I want to try ASVA")
        assert "team" in _reply(db, num, word).lower(), word


def test_asking_again_after_the_pitch_counts_as_interest():
    db, num = _DB(), "919555555555"
    _reply(db, num, "ASVA details please")
    assert "team" in _reply(db, num, "what is the asva price").lower()


def test_global_switch_off_silences_everything():
    from app.services import assistant
    db = _DB()
    assistant.set_assistant_enabled(db, False)
    assert _reply(db, "919666666666", "I want to try ASVA") == ""


def test_copy_is_english_and_has_no_dashes():
    db, num = _DB(), "919777777777"
    for r in (_reply(db, num, "I want to try ASVA"), _reply(db, num, "YES")):
        assert "—" not in r and "–" not in r
        for h in ("bhejein", "karta hai", "aapke", "humari"):
            assert h not in r.lower()


# ── Owners: commands work, personal chat is left to the founder ───────────
def test_owner_free_text_gets_no_automated_reply():
    for msg in ("hello sir", "kal milte hai", "thank you for the help", "ok"):
        assert bot._owner_fallback(msg, "english") == "", msg


def test_owner_mistyped_command_still_gets_help():
    r = bot._owner_fallback("chek ramesh", "english")
    assert "CHECK ramesh" in r and "LIST" in r
