"""Every admin page must RENDER (HTTP 200, no 'Internal Server Error') in all
three UI languages. This is the guard that was missing when a bool was passed to
the trilingual translator and the Today + Payments pages started 500ing in prod:
the money-math unit tests all passed because none of them actually rendered a
page. This does, for english / hinglish / hindi.
"""
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.routers import admin


# A business row rich enough for every page's rendering to run on empty data.
BIZ = {
    "id": "b1", "business_name": "Test Traders", "agent_token": "tok",
    "weekly_off_day": None, "blackout_dates": [], "reminder_style": "standard",
    "reminder_custom_line": "", "reminder_hour": 10, "msg_language": "hinglish",
    "owner_language": "hinglish", "discount_pct": 0, "plan": "starter",
    "upi_vpa": "shop@upi", "upi_vpa_2": "", "upi_vpa_3": "", "whatsapp_number": "919000000000",
    "reminder_cadence": None, "bank_account_name": "", "bank_account_no": "", "bank_ifsc": "",
    "bank_name": "", "overdue_repeat_days": 7, "overdue_max_repeats": 20,
    "reminder_batches": None, "plan_expires_on": "2026-09-30", "catchup_date": None,
    "catchup_action": None, "wa_ready": True, "wa_checked_at": None,
    "tally_deposit_ledgers": ["Cash"], "share_data": True,
}

CLIENT = {
    "id": "c1", "name": "A Customer", "whatsapp_number": "919111111111",
    "credit_days": 30, "reminders_enabled": True, "excluded": False,
    "tally_ledger_name": "A Customer", "language": "hinglish", "reminder_batch": 1,
    "reminder_anchor": None, "created_at": "2026-01-01T00:00:00+00:00",
}


class _Resp:
    def __init__(self, data):
        self.data = data


class _Tbl:
    """Chainable query stub: any filter/order returns self; execute() returns a
    sensible default per table (one biz, one client, else empty). insert() echoes
    the row back WITH an id, like Supabase does, so create/confirm flows work."""
    def __init__(self, name):
        self.name = name
        self._ins = None

    def insert(self, row):
        self._ins = dict(row)
        self._ins.setdefault("id", "row1")
        return self

    def __getattr__(self, _):
        # select/eq/in_/order/limit/range/gte/lte/or_/update/delete/... -> self
        return lambda *a, **k: self

    def execute(self):
        if self._ins is not None:
            return _Resp([self._ins])
        if self.name == "businesses":
            return _Resp([dict(BIZ)])
        if self.name == "clients":
            return _Resp([dict(CLIENT)])
        return _Resp([])


class FakeDB:
    def table(self, name):
        return _Tbl(name)


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    monkeypatch.setattr(admin, "require_db", lambda: FakeDB())


LANGS = ["english", "hinglish", "hindi"]


def _ok(resp):
    body = getattr(resp, "body", b"") or b""
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(resp)
    assert resp.status_code == 200, "status %s" % resp.status_code
    assert "Internal Server Error" not in text
    return text


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", [
    "admin_page", "admin_today", "admin_payments", "admin_reminders",
    "admin_analytics", "admin_accounts", "admin_setup",
])
def test_admin_pages_render(page, lang):
    fn = getattr(admin, page)
    resp = asyncio.run(fn(token="tok", lang=lang))
    _ok(resp)


@pytest.mark.parametrize("lang", LANGS)
def test_party_page_renders(lang):
    resp = asyncio.run(admin.admin_party(token="tok", client_id="c1", lang=lang))
    _ok(resp)


def test_manual_payment_enqueue_does_not_crash():
    # Regression: admin_promise_action used names.clean_display but `names` was not
    # imported at that scope -> NameError -> the manual "Enter payment / Post to
    # Tally" 500'd ("Could not save") while the WhatsApp PAID path worked. Exercise
    # the POST endpoint end to end (page-render tests never hit it).
    from app.routers.admin import PromiseActionPayload
    out = asyncio.run(admin.admin_promise_action(
        PromiseActionPayload(token="tok", client_id="c1", action="enqueue",
                             amount=5000, deposit_ledger="INDIAN BANK",
                             receipt_date="2026-08-28")))
    assert out.get("ok") is True


def test_dashboard_collapses_to_cards_on_narrow():
    # P2: the 9-column parties table must carry per-cell labels + a narrow-width
    # media query so WhatsApp + Actions never scroll off-screen on a small laptop.
    text = _ok(asyncio.run(admin.admin_page(token="tok", lang="hinglish")))
    assert "@media (max-width:760px)" in text
    assert 'data-label="Actions"' in text
    assert 'data-label="WhatsApp"' in text
    assert 'class="c-act"' in text


@pytest.mark.parametrize("lang", LANGS)
def test_today_has_language_runtime(lang):
    # The Today page must ship the TT() runtime so its JS-built strings localise.
    text = _ok(asyncio.run(admin.admin_today(token="tok", lang=lang)))
    assert "function TT(" in text
    assert ('const LANG="%s"' % lang) in text


# ── Indian number grouping ───────────────────────────────────────────────────
# Python's ',' format is WESTERN (2,829,280). A shopkeeper counting in lakhs
# misreads that at a glance; it must be 28,29,452.
import re as _re
from app.routers.admin import _inr, _inr_digits


def test_inr_digits_is_indian_grouped():
    assert _inr_digits(2829452) == "28,29,452"
    assert _inr_digits(188182) == "1,88,182"
    assert _inr_digits(256104108) == "25,61,04,108"
    assert _inr_digits(999) == "999"
    assert _inr_digits(0) == "0"


def test_inr_keeps_its_prefix_once():
    assert _inr(2829452) == "Rs 28,29,452"


def test_dashboard_has_no_western_grouped_rupees():
    """A western-grouped amount (3 digits, comma, exactly 3 digits, comma, 3
    digits) must never reach the page - that is the 2,829,280 bug."""
    resp = asyncio.run(admin.admin_page(token="tok", lang="english"))
    html = _ok(resp)
    bad = _re.findall(r"(?:&#8377;|₹|Rs\s?)\d{1,3},\d{3},\d{3}\b", html)
    assert not bad, "western-grouped rupee amounts leaked: %s" % bad[:5]
