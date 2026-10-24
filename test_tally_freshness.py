"""Never chase a customer on figures we cannot stand behind.

A shop does not leave TallyPrime open all day. While it is shut, ASVA's picture
of who owes what stops moving but the reminder cadence does not, so a customer
who paid on Tuesday gets chased on Thursday for money they already sent. That
is worse than silence: it is the relationship damage the product exists to
prevent, and the owner hears about it from the customer.

Moving WhatsApp onto the server made this sharper. Before, a shut Tally usually
meant a shut laptop, which meant nothing went out - the two failures masked
each other. The server sends regardless.

So past 72 hours the reminders hold and the owner is told once, in a message
they can act on. The tests below pin the hold, the two ways it must NOT fire
(unknown freshness, and an owner who said to carry on), and the recovery.
"""
from __future__ import annotations

import datetime as dt
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.config import settings
from app.services import tally_freshness as fresh


def _ago(hours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()


# ── is_stale ──────────────────────────────────────────────────────────────
def test_a_shop_that_synced_an_hour_ago_is_fresh():
    assert fresh.is_stale({"last_tally_sync_at": _ago(1)}) is False


def test_a_shop_that_has_not_synced_in_four_days_is_stale():
    assert fresh.is_stale({"last_tally_sync_at": _ago(96)}) is True


def test_the_boundary_is_the_configured_threshold():
    just_under = fresh.is_stale({"last_tally_sync_at": _ago(settings.tally_stale_hold_h - 1)})
    just_over = fresh.is_stale({"last_tally_sync_at": _ago(settings.tally_stale_hold_h + 1)})
    assert just_under is False and just_over is True


def test_unknown_freshness_never_holds():
    """Before migration 060 the column does not exist. A hold is a whole shop's
    collections stopped, so it must only ever fire on a figure we actually read
    - never on a missing one."""
    assert fresh.is_stale({}) is False
    assert fresh.is_stale({"last_tally_sync_at": None}) is False
    assert fresh.is_stale({"last_tally_sync_at": "not a timestamp"}) is False


def test_the_owner_can_say_carry_on():
    biz = {"last_tally_sync_at": _ago(96), "tally_stale_ack_at": _ago(1)}
    assert fresh.is_stale(biz) is False, "the owner answered CHASE; it is their ledger"


def test_that_answer_expires():
    biz = {"last_tally_sync_at": _ago(300),
           "tally_stale_ack_at": _ago(settings.tally_stale_ack_h + 1)}
    assert fresh.is_stale(biz) is True


def test_the_feature_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(settings, "tally_stale_hold_h", 0)
    assert fresh.is_stale({"last_tally_sync_at": _ago(500)}) is False


# ── stale_ids, which is what the sweep actually calls ─────────────────────
class _Boom:
    """A database whose businesses table predates migration 060."""

    def table(self, _):
        raise RuntimeError('column "last_tally_sync_at" does not exist')


def test_a_missing_column_holds_nothing_rather_than_everything():
    """The failure mode that matters: if this raised, or returned everything,
    an unapplied migration would silently stop every reminder for every shop."""
    assert fresh.stale_ids(_Boom(), ["b1", "b2"]) == set()


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _):
        return self

    def select(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return MagicMock(data=self.rows)


def test_stale_ids_picks_only_the_stale_ones():
    db = _DB([
        {"id": "fresh", "last_tally_sync_at": _ago(2), "tally_stale_ack_at": None},
        {"id": "stale", "last_tally_sync_at": _ago(96), "tally_stale_ack_at": None},
        {"id": "acked", "last_tally_sync_at": _ago(96), "tally_stale_ack_at": _ago(1)},
        {"id": "unknown", "last_tally_sync_at": None, "tally_stale_ack_at": None},
    ])
    assert fresh.stale_ids(db, ["fresh", "stale", "acked", "unknown"]) == {"stale"}


def test_no_ids_means_no_query():
    assert fresh.stale_ids(_Boom(), []) == set()


# ── what the owner is told ────────────────────────────────────────────────
def test_the_label_is_something_a_shopkeeper_can_act_on():
    """'73.4 hours' is not a thing anyone can picture. A weekday is."""
    assert fresh.last_sync_label({"last_tally_sync_at": _ago(2)}).startswith("today")
    assert fresh.last_sync_label({"last_tally_sync_at": _ago(30)}).startswith("yesterday")
    assert fresh.last_sync_label({"last_tally_sync_at": _ago(72)}) in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def test_the_notice_renders_with_nothing_left_unfilled():
    from app.services.wa_notice import _STALE_EN, _STALE_HI
    for tpl in (_STALE_EN, _STALE_HI):
        body = tpl.format(name="Supreme Traders", when="Tuesday", days=3)
        assert "{" not in body and "}" not in body
        assert "—" not in body and "–" not in body       # house rule


def test_the_notice_says_what_stopped_what_to_do_and_offers_the_answer():
    from app.services.wa_notice import _STALE_EN
    body = _STALE_EN.format(name="Supreme Traders", when="Tuesday", days=3)
    assert "PAUSED" in body                       # what stopped
    assert "TallyPrime" in body and "Refresh" in body   # how to fix it
    assert "CHASE" in body                        # the one answer only they can give
    assert "Tuesday" in body                      # how old, in human terms


def test_the_recovery_notice_exists_so_the_loop_closes():
    from app.services.wa_notice import _FRESH_EN, FRESH, STALE
    assert STALE != FRESH, "the two notices must dedup separately"
    assert "restarted" in _FRESH_EN.format(name="X")


# ── the sweep ─────────────────────────────────────────────────────────────
def test_the_sweep_consults_freshness_before_sending():
    import inspect

    from app.jobs import reminder_sweep
    src = inspect.getsource(reminder_sweep.run)
    assert "fresh.stale_ids" in src, "the sweep does not check how old the figures are"
    assert "k not in stale" in src, "stale shops are not actually dropped"


def test_a_bare_chase_answers_the_question_and_a_named_one_still_does_not():
    """CHASE <name> has meant 'resume this one party' for months. The bare verb
    must not steal it."""
    import inspect

    from app.services import bot
    src = inspect.getsource(bot)
    assert 'if upper == "CHASE":' in src
    assert "_fresh.ack_stale(db, business_id)" in src
    assert 'chase_match = re.match(r"CHASE\\s+(.+)", upper)' in src
