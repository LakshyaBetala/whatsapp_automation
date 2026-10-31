"""The disconnected-WhatsApp notice: told once, by the bot, and never nagging.

A silent WhatsApp logout is the worst failure ASVA has, because nothing looks
broken while the shop stops chasing anyone. So the owner must hear about it.
These tests pin the rules that keep that one useful message from turning into
the thing owners mute: nothing for a reconnect blip, one notice a day while it
stays down, nothing at night, and a "sending again" line only for an owner we
actually interrupted.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import wa_notice


NOW = _dt.datetime.now(_dt.timezone.utc)


def _ago(**kw) -> str:
    return (NOW - _dt.timedelta(**kw)).isoformat()


BIZ = {"id": "b1", "business_name": "Gupta Electricals", "whatsapp_number": "919444294894",
       "msg_language": "english", "owner_language": None}
ROW_DOWN = {"id": "b1", "wa_ready": False, "queued": 7}
ROW_UP = {"id": "b1", "wa_ready": True, "queued": 0}
PROBLEM = {"kind": "wa_down", "business_id": "b1", "severity": "critical",
           "title": "x", "body": "y"}


class _Resp:
    def __init__(self, data):
        self.data = data


class _Tbl:
    def __init__(self, name, state):
        self.name, self.state = name, state
        self.cols = None

    def select(self, cols="*", *a, **k):
        # Honour the column list. A fake that hands back every column no matter
        # what was asked for cannot catch a forgotten one, and a forgotten
        # last_tally_sync_at is exactly what shipped.
        self.cols = None if "*" in cols else [c.strip() for c in cols.split(",")]
        return self

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        if self.name == "businesses":
            biz = self.state.get("biz")
            if not biz:
                return _Resp([])
            row = dict(biz)
            if self.cols is not None:
                row = {k: v for k, v in row.items() if k in self.cols}
            return _Resp([row])
        if self.name == "alert_log":
            at = self.state.get("down_since")
            return _Resp([{"created_at": at}] if at else [])
        if self.name == "messages":
            # The query is ordered newest-first and limited to 1, so whichever
            # notice the state names as the latest is what comes back.
            return _Resp([])
        return _Resp([])


class _MsgTbl(_Tbl):
    """messages, where the dedup lookup filters by template_name."""

    def __init__(self, name, state):
        super().__init__(name, state)
        self.template = None

    def eq(self, col, val):
        if col == "template_name":
            self.template = val
        return self

    def execute(self):
        at = self.state.get("notices", {}).get(self.template)
        if not at:
            return _Resp([])
        # notice_n lets a test say "this shop has already had N of these in the
        # current episode", which is what the two-nudge budget counts.
        n = int(self.state.get("notice_n", {}).get(self.template, 1))
        return _Resp([{"created_at": at}] * n)


class FakeDB:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _MsgTbl(name, self.state) if name == "messages" else _Tbl(name, self.state)


def _run(monkeypatch, state, *, problems=(PROBLEM,), rows=(ROW_DOWN,), in_window=True):
    sent = []

    async def _notify(business_id, text, *, template_name="openwa_custom"):
        sent.append({"business_id": business_id, "text": text, "template": template_name})
        return {"sent": True}

    monkeypatch.setattr(wa_notice.whatsapp, "notify_owner", _notify)
    monkeypatch.setattr(wa_notice, "within_send_window", lambda *a, **k: in_window)
    db = FakeDB(state)
    out = asyncio.run(wa_notice.run(db, list(problems), {"businesses": [dict(r) for r in rows]}))
    return out, sent


def _state(**over):
    s = {"biz": dict(BIZ), "down_since": _ago(hours=2), "notices": {}, "notice_n": {}}
    s.update(over)
    return s


def test_tells_the_owner_once_with_what_is_waiting(monkeypatch):
    out, sent = _run(monkeypatch, _state())
    assert out == 1 and len(sent) == 1
    assert sent[0]["template"] == "wa_down_notice"
    body = sent[0]["text"]
    assert "not connected" in body and "7 messages are waiting" in body
    assert "Scan the QR" in body and "Nothing is lost" in body


def test_stays_quiet_for_a_reconnect_blip(monkeypatch):
    out, sent = _run(monkeypatch, _state(down_since=_ago(minutes=5)))
    assert out == 0 and sent == []


def test_does_not_repeat_the_same_day(monkeypatch):
    state = _state(notices={"wa_down_notice": _ago(hours=3)})
    out, sent = _run(monkeypatch, state)
    assert out == 0 and sent == []


def test_sends_the_second_nudge_two_days_later(monkeypatch):
    """Two messages per outage, spaced out - not one every morning."""
    state = _state(down_since=_ago(hours=50),
                   notices={"wa_down_notice": _ago(hours=49)})
    out, sent = _run(monkeypatch, state)
    assert out == 1 and sent[0]["template"] == "wa_down_notice"


def test_does_not_send_a_third_nudge_ever(monkeypatch):
    """The budget is two per outage. After that the owner either knows or has
    left, and a daily message is just something to mute."""
    state = _state(down_since=_ago(hours=100),
                   notices={"wa_down_notice": _ago(hours=60)},
                   notice_n={"wa_down_notice": 2})
    out, sent = _run(monkeypatch, state)
    assert out == 0 and sent == []


def test_gives_up_on_a_shop_that_has_been_down_a_week(monkeypatch):
    """A week of silence is a shop that stopped using ASVA. Nudging it again is
    noise from a product they already left."""
    state = _state(down_since=_ago(days=8))
    out, sent = _run(monkeypatch, state)
    assert out == 0 and sent == []


def test_a_second_nudge_still_waits_out_the_gap(monkeypatch):
    state = _state(down_since=_ago(hours=50),
                   notices={"wa_down_notice": _ago(hours=20)})
    out, sent = _run(monkeypatch, state)
    assert out == 0 and sent == []


def test_stays_quiet_at_night(monkeypatch):
    out, sent = _run(monkeypatch, _state(), in_window=False)
    assert out == 0 and sent == []


def test_says_nothing_when_no_shop_is_down(monkeypatch):
    out, sent = _run(monkeypatch, _state(down_since=None), problems=(), rows=(ROW_UP,))
    assert out == 0 and sent == []


def test_confirms_when_it_comes_back_but_only_once(monkeypatch):
    state = _state(notices={"wa_down_notice": _ago(hours=4)})
    out, sent = _run(monkeypatch, state, problems=(), rows=(ROW_UP,))
    assert out == 1 and sent[0]["template"] == "wa_back_notice"
    assert "connected again" in sent[0]["text"]

    state = _state(notices={"wa_down_notice": _ago(hours=4), "wa_back_notice": _ago(hours=3)})
    out, sent = _run(monkeypatch, state, problems=(), rows=(ROW_UP,))
    assert out == 0 and sent == []


def test_no_recovery_line_for_an_owner_we_never_interrupted(monkeypatch):
    out, sent = _run(monkeypatch, _state(), problems=(), rows=(ROW_UP,))
    assert out == 0 and sent == []


def test_english_unless_the_shop_chose_hinglish(monkeypatch):
    out, sent = _run(monkeypatch, _state())
    assert "aapka" not in sent[0]["text"]
    hing = _state(biz=dict(BIZ, msg_language="hinglish"))
    out, sent = _run(monkeypatch, hing)
    assert "aapka WhatsApp abhi ASVA se connected nahi hai" in sent[0]["text"]


def test_a_shop_with_no_number_is_skipped(monkeypatch):
    out, sent = _run(monkeypatch, _state(biz=dict(BIZ, whatsapp_number="")))
    assert out == 0 and sent == []


# ── The stale-figures notice ──────────────────────────────────────────────
# This branch had no coverage at all, which is how it shipped reading every
# shop as "1 days old" and messaging shops that last synced in August every
# other day. The cause was a missing column in run()'s own select, so these
# tests build the business row the way the real query returns it.

ROW_UP_OK = {"id": "b1", "wa_ready": True, "queued": 0}


def _stale_state(hours_since_sync, **over):
    biz = dict(BIZ)
    if hours_since_sync is not None:
        biz["last_tally_sync_at"] = _ago(hours=hours_since_sync)
    s = {"biz": biz, "down_since": None, "notices": {}, "notice_n": {}}
    s.update(over)
    return s


def _run_stale(monkeypatch, state):
    return _run(monkeypatch, state, problems=(), rows=(ROW_UP_OK,))


def test_tells_a_shop_its_figures_are_stale(monkeypatch):
    out, sent = _run_stale(monkeypatch, _stale_state(96))      # 4 days
    assert out == 1 and sent[0]["template"] == "tally_stale_notice"
    assert "4 days old" in sent[0]["text"]


def test_says_nothing_before_the_three_day_floor(monkeypatch):
    out, sent = _run_stale(monkeypatch, _stale_state(30))      # ~1 day
    assert out == 0 and sent == []


def test_gives_up_on_a_shop_stale_for_more_than_a_week(monkeypatch):
    """These shops last synced in August. They were getting a message every
    other day telling them their figures were one day old."""
    out, sent = _run_stale(monkeypatch, _stale_state(1735))     # ~72 days
    assert out == 0 and sent == []


def test_two_nudges_is_the_whole_budget(monkeypatch):
    state = _stale_state(120, notices={"tally_stale_notice": _ago(hours=60)},
                         notice_n={"tally_stale_notice": 2})
    out, sent = _run_stale(monkeypatch, state)
    assert out == 0 and sent == []


def test_the_second_nudge_waits_out_the_gap(monkeypatch):
    state = _stale_state(120, notices={"tally_stale_notice": _ago(hours=20)})
    out, sent = _run_stale(monkeypatch, state)
    assert out == 0 and sent == []


def test_without_a_sync_date_it_stays_quiet(monkeypatch):
    """The exact production bug: run() did not select last_tally_sync_at, so
    every gate read the shop as freshly synced and messaged it forever."""
    out, sent = _run_stale(monkeypatch, _stale_state(None))
    assert out == 0 and sent == []
