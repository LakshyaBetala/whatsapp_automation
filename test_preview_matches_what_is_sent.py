"""Preview must show the message the customer will actually get.

It is the only place an owner checks their wording before it goes to a
customer, so a preview that quietly renders the standard tone while the shop
is set to firm, or drops the shop's own closing line, is worse than no
preview: it is confidently wrong, and it makes every later preview look
unchanged.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from fastapi.testclient import TestClient

from app.main import app
from app.routers import admin

BIZ = {
    "id": "b1", "business_name": "Test Traders", "plan": "pro",
    "plan_expires_on": "2027-12-31", "msg_language": "english", "owner_language": "english",
    "reminder_style": "firm", "reminder_custom_line": "Please clear this week.",
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


# Through the real request path: the point of this file is what the endpoint
# does with parameters the CALLER omits, and those defaults only exist when
# FastAPI resolves them.
client = TestClient(app)


def _preview(**kw):
    r = client.get("/admin/preview", params={"token": "tok", **kw})
    assert r.status_code == 200, r.text
    return r.json().get("message", "")


def test_it_uses_the_shops_own_closing_line():
    """The owner typed this line. If the preview omits it, the preview is of a
    message that does not exist."""
    assert "Please clear this week." in _preview()


def test_it_renders_with_the_shops_own_tone():
    """Tone picks the chasing schedule, and a worded variant where the language
    has one. English deliberately has none, so the English preview is rendered
    as standard on purpose; Hinglish is where the tone reaches the template,
    and it must be the SHOP'S tone rather than "standard" as it used to be."""
    seen = {}
    import app.services.templates as tpl
    real = tpl.render

    def spy(key, lang, style="standard", **kw):
        seen["style"] = style
        return real(key, lang, style=style, **kw)

    tpl.render = spy
    try:
        _preview(language="hinglish")
    finally:
        tpl.render = real
    assert seen.get("style") == "firm", seen

    # And English is standard by design, not by accident.
    tpl.render = spy
    try:
        _preview(language="english")
    finally:
        tpl.render = real
    assert seen.get("style") == "standard", "English has no tone variants"


def test_an_explicit_setting_still_wins():
    """So an unsaved choice can be previewed before it is saved."""
    assert "Please clear this week." not in _preview(custom_line="")
    assert "Try me instead." in _preview(custom_line="Try me instead.")


def test_a_shop_with_no_tone_set_still_previews():
    BIZ["reminder_style"] = None
    BIZ["reminder_custom_line"] = None
    try:
        msg = _preview()
        assert msg and "Ramesh Traders" in msg
    finally:
        BIZ["reminder_style"] = "firm"
        BIZ["reminder_custom_line"] = "Please clear this week."


def test_the_page_asks_the_browser_not_to_cache_it():
    """Two batches with the same language and discount make the same URL; a
    cached reply is the other way a preview looks stuck."""
    html = client.get("/admin/reminders", params={"token": "tok", "lang": "english"}).text
    assert "cache:'no-store'" in html
