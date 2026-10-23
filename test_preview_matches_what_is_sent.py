"""The preview must BE the message the sender composes, not a copy of it.

It drifted once and nobody noticed for months: reminders moved to a tappable
https pay page, and the Preview button carried on rendering the old
`reminder_en` template with a raw upi:// link in it. The owner was checking
their wording against a message their customers had not received in a long
time, and every later preview looked "stuck on the old one".

So the test is not "does the preview contain some expected words" - that is
exactly what let it drift. It asks the sender what it would send, asks the
preview what it shows, and requires them to be the same string.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from fastapi.testclient import TestClient

from app.main import app
from app.routers import admin
from app.services import bot as bot_svc

BIZ = {
    "id": "b1", "business_name": "Test Traders", "plan": "pro",
    "plan_expires_on": "2027-12-31", "msg_language": "english", "owner_language": "english",
    "reminder_style": "standard", "reminder_custom_line": None,
    "reminder_hour": 11, "discount_pct": 0, "upi_vpa": "shop@ybl",
    "whatsapp_number": "919000000000", "reminder_batches": None,
    "weekly_off_day": None, "blackout_dates": None, "reminder_cadence": None,
    "overdue_repeat_days": 7, "overdue_max_repeats": 3, "catchup_date": None,
    "catchup_action": None, "wa_ready": True, "wa_checked_at": None,
    "setup_problem": None, "wa_transport": "server", "upi_vpa_2": None, "upi_vpa_3": None,
    "bank_account_name": None, "bank_account_no": None, "bank_ifsc": None, "bank_name": None,
}


class _R:
    def __init__(self, data):
        self.data = data


class _T:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        return _R([dict(BIZ)] if self.name == "businesses" else [])


class FakeDB:
    def table(self, name):
        return _T(name)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(admin, "require_db", lambda: FakeDB())


client = TestClient(app)


def _preview(**kw) -> str:
    r = client.get("/admin/preview", params={"token": "tok", **kw})
    assert r.status_code == 200, r.text
    return r.json().get("message", "")


def _what_the_sender_would_send(lang: str = "english", closing: str | None = None,
                                **batch_over) -> str:
    """Ask the composer itself, the way the sweep calls it."""
    today = admin._today()
    biz = dict(BIZ)
    if closing is not None:
        biz["reminder_custom_line"] = closing
    batch = {"name": "Preview", "lang": lang, "upi": "", "hour": 11, "disc": 0, "line": ""}
    batch.update(batch_over)
    biz["reminder_batches"] = [batch]
    entry = {
        "client": {"id": "preview", "name": "Ramesh Traders",
                   "whatsapp_number": "919000000000", "reminder_batch": 0, "language": "hi"},
        "total": Decimal("12500"),
        "bills": [{"id": "preview", "invoice_number": "2526RTC0203",
                   "invoice_date": (today - dt.timedelta(days=35)).isoformat(),
                   "due_date": (today - dt.timedelta(days=5)).isoformat(),
                   "outstanding": Decimal("12500")}],
    }
    return asyncio.run(bot_svc._send_consolidated_reminder(biz, entry, dry_run=True))


@pytest.mark.parametrize("lang", ["english", "hinglish"])
def test_the_preview_is_exactly_what_would_be_sent(lang):
    assert _preview(language=lang) == _what_the_sender_would_send(lang)


def test_it_shows_the_tappable_pay_page_not_a_raw_upi_string():
    """WhatsApp does not linkify upi://, which is why reminders send a pay
    page. A preview showing the raw string is showing the old message."""
    msg = _preview()
    assert "/pay/" in msg, msg[-200:]
    assert "upi://" not in msg and "&pn=" not in msg, "the old raw UPI link is back"


def test_it_carries_the_closing_line_the_owner_saved():
    """Settings stores this line and shows it back, so it must actually be in
    the message. It was being dropped on the way out."""
    msg = _preview(custom_line="Please clear this week.")
    assert "Please clear this week." in msg
    assert msg == _what_the_sender_would_send(closing="Please clear this week.")


def test_a_cash_batch_previews_the_cash_message_with_no_link():
    from app.services.batches import CASH_SENTINEL
    msg = _preview(upi=CASH_SENTINEL)
    assert "/pay/" not in msg, "a cash customer must get no payment link"
    assert msg == _what_the_sender_would_send(upi=CASH_SENTINEL)


def test_a_discount_changes_the_preview():
    assert _preview(discount_pct=2) == _what_the_sender_would_send(disc=2)
    assert _what_the_sender_would_send(disc=2) != _what_the_sender_would_send(disc=0)


def test_the_page_asks_the_browser_not_to_cache_it():
    """Two batches with the same settings make the same URL; a cached reply is
    the other way a preview looks stuck."""
    html = client.get("/admin/reminders", params={"token": "tok", "lang": "english"}).text
    assert "cache:'no-store'" in html
    assert "batch:String(i)" in html, "the preview must say which batch it is previewing"


def test_dry_run_sends_nothing(monkeypatch):
    """The flag must never become a way to send by accident."""
    sent = []

    async def _boom(*a, **k):
        sent.append(k)
        return {}

    monkeypatch.setattr(bot_svc.whatsapp, "send_template", _boom)
    _what_the_sender_would_send()
    assert sent == [], "dry_run reached the sender"
