"""Non-owner -> join funnel (bot._prospect_reply).

The ASVA marketing number IS the assistant/bot number, so a non-owner messaging
it (off the poster) is a lead, not a stranger. We must never bounce them: an
inquiry gets an invite with a YES call-to-action, and a YES routes to human
follow-up. Every reply promises the free first month, in Hinglish + English.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot


def _reply(text):
    return bot._prospect_reply(text, text.upper().strip())


def test_interested_words_route_to_followup():
    for word in ("YES", "haan", "Ha", "JOIN", "interested", "chahiye", "signup"):
        r = _reply(word)
        assert "team" in r.lower() and "free" in r.lower(), word


def test_interested_within_a_sentence():
    r = _reply("haan bhai mujhe chahiye")
    assert "team" in r.lower() and "free" in r.lower()


def test_inquiry_gets_the_invite_with_cta():
    for msg in ("kya hai ye?", "hello", "poster dekha", "namaste"):
        r = _reply(msg)
        assert "ASVA" in r
        assert "YES" in r                      # clear call to action
        assert "free" in r.lower()


def test_never_bounces_a_prospect():
    # No reply should ever say "only for registered owners" (the old bounce).
    for msg in ("YES", "kya hai", "random text", "hello"):
        assert "registered" not in _reply(msg).lower()


def test_no_em_or_en_dashes():
    for msg in ("YES", "kya hai ye"):
        r = _reply(msg)
        assert "—" not in r and "–" not in r


def test_greeting_word_is_an_inquiry_not_interested():
    # HI/HELLO must lead to the invite (with YES cta), not the "team will contact"
    # path, since a greeting is not yet a commitment.
    r = _reply("hi")
    assert "YES" in r and "team" not in r.lower()
