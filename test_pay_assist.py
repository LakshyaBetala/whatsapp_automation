"""The quiet-tap assist: offered once, to the right people, within bounds.

A tap on the pay page is intent, not money. If nothing lands, ASVA offers help
once. These tests pin the rules that keep that from becoming spam or a privacy
leak: one send per tap, nothing if the bill is already settled, nothing outside
the send window or for a sleeping shop, and wording that never tells the
customer they were watched.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs import pay_assist


NOW = _dt.datetime.now(_dt.timezone.utc)
TAP_AT = (NOW - _dt.timedelta(minutes=30)).isoformat()

BIZ = {"id": "b1", "business_name": "Gupta Electricals", "plan": "starter",
       "plan_expires_on": (NOW + _dt.timedelta(days=30)).date().isoformat(),
       "msg_language": "english", "reminder_batches": None, "discount_pct": 0,
       "reminder_hour": 11, "last_seen": NOW.isoformat(), "pay_assist": True}
CLIENT = {"id": "c1", "name": "Sharma Traders", "whatsapp_number": "919111111111",
          "reminder_batch": 0, "excluded": False, "reminders_enabled": True}
TAP = {"id": "e1", "business_id": "b1", "client_id": "c1", "amount": 18400,
       "created_at": TAP_AT}


class _Resp:
    def __init__(self, data):
        self.data = data


class _Tbl:
    def __init__(self, name, state):
        self.name, self.state = name, state
        self._update = None

    def update(self, row):
        self._update = dict(row)
        return self

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        if self._update is not None:
            self.state.setdefault("updates", []).append((self.name, self._update))
            return _Resp([])
        if self.name == "pay_link_events":
            return _Resp(list(self.state.get("taps", [])))
        if self.name == "businesses":
            return _Resp([dict(self.state["biz"])] if self.state.get("biz") else [])
        if self.name == "clients":
            return _Resp([dict(self.state["client"])] if self.state.get("client") else [])
        if self.name == "bills":
            return _Resp(self.state.get("open_bills", [{"id": "bill1"}]))
        return _Resp([])


class FakeDB:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _Tbl(name, self.state)


def _run(monkeypatch, state, *, in_window=True):
    sent = []

    async def _send(**kw):
        sent.append(kw)
        return {"sent": True}

    monkeypatch.setattr(pay_assist, "get_client", lambda: FakeDB(state))
    monkeypatch.setattr(pay_assist.whatsapp, "send_message", _send)
    from app.jobs import outbox_sweep
    monkeypatch.setattr(outbox_sweep, "within_send_window", lambda *a, **k: in_window)
    out = asyncio.run(pay_assist.run())
    return out, sent, state.get("updates", [])


def _state(**over):
    s = {"taps": [dict(TAP)], "biz": dict(BIZ), "client": dict(CLIENT),
         "open_bills": [{"id": "bill1"}]}
    s.update(over)
    return s


def test_offers_help_once_and_stamps_before_sending(monkeypatch):
    out, sent, updates = _run(monkeypatch, _state())
    assert out["sent"] == 1 and len(sent) == 1
    # the stamp is what makes it once-only, so it must have been written
    assert any(t == "pay_link_events" and "assist_sent_at" in u for t, u in updates)
    assert sent[0]["to_number"] == "919111111111"
    assert sent[0]["template_name"] == "pay_assist"


def test_says_nothing_when_the_bill_is_already_settled(monkeypatch):
    out, sent, _ = _run(monkeypatch, _state(open_bills=[]))
    assert out["sent"] == 0 and sent == []


def test_says_nothing_outside_the_send_window(monkeypatch):
    out, sent, _ = _run(monkeypatch, _state(), in_window=False)
    assert out["sent"] == 0 and sent == []


def test_says_nothing_when_the_shop_switched_it_off(monkeypatch):
    biz = dict(BIZ, pay_assist=False)
    out, sent, _ = _run(monkeypatch, _state(biz=biz))
    assert out["sent"] == 0 and sent == []


def test_says_nothing_for_a_dormant_shop(monkeypatch):
    old = (NOW - _dt.timedelta(days=30)).isoformat()
    out, sent, _ = _run(monkeypatch, _state(biz=dict(BIZ, last_seen=old)))
    assert out["sent"] == 0 and sent == []


def test_says_nothing_for_an_excluded_party_or_one_without_a_number(monkeypatch):
    for bad in ({"excluded": True}, {"whatsapp_number": ""}):
        out, sent, _ = _run(monkeypatch, _state(client=dict(CLIENT, **bad)))
        assert out["sent"] == 0 and sent == []


@pytest.mark.parametrize("en", [True, False])
def test_wording_never_tells_the_customer_they_were_watched(en):
    text = pay_assist._text("Sharma Traders", "Gupta Electricals", en).lower()
    for leak in ("opened", "clicked", "tapped", "link you", "khola", "dabaya"):
        assert leak not in text, f"assist message reveals behaviour: {leak}"
    # it offers help and lets them ignore it, and never claims a payment
    assert ("ignore" in text) or ("ignore karein" in text)
    assert "paid" not in text and "mil gaya" not in text


def test_amount_and_bill_are_never_in_the_message():
    text = pay_assist._text("Sharma Traders", "Gupta Electricals", True)
    assert "18,400" not in text and "18400" not in text and "#" not in text
