"""Non-owner -> join funnel (bot._prospect_reply).

The ASVA marketing number IS the assistant/bot number, so a non-owner messaging
it (off the poster) is a lead, not a stranger. The funnel is ENGLISH ONLY: an
inquiry gets an invite with a YES call-to-action, and a YES routes to the human
follow-up. Never bounce a prospect; never use em/en dashes.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot


def _reply(text):
    return bot._prospect_reply("919100000000", text, text.upper().strip())


def test_inquiry_gets_english_invite_with_cta():
    for msg in ("hi", "hello", "kya hai ye?", "poster dekha"):
        r = _reply(msg)
        assert "ASVA" in r
        assert "YES" in r                        # clear call to action
        assert "free" in r.lower() and "September" in r


def test_interested_words_route_to_followup():
    for word in ("YES", "haan", "Ha", "JOIN", "interested", "chahiye", "signup"):
        r = _reply(word)
        assert "team" in r.lower() and "free" in r.lower(), word


def test_interested_within_a_sentence():
    r = _reply("yes please i want it")
    assert "team" in r.lower() and "free" in r.lower()


def test_english_only_no_hinglish():
    # No Hinglish words leak into the funnel copy.
    for msg in ("hi", "YES"):
        low = _reply(msg).lower()
        for h in ("bhejein", "karta hai", "aapke", "humari", "bhasha"):
            assert h not in low, (msg, h)


def test_never_bounces_a_prospect():
    for msg in ("YES", "kya hai", "random text", "hello"):
        assert "registered" not in _reply(msg).lower()


def test_no_em_or_en_dashes():
    for msg in ("hello", "YES"):
        r = _reply(msg)
        assert "—" not in r and "–" not in r
