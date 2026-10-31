"""Every admin page must RENDER (HTTP 200, no 'Internal Server Error') in all
three UI languages. This is the guard that was missing when a bool was passed to
the trilingual translator and the Today + Payments pages started 500ing in prod:
the money-math unit tests all passed because none of them actually rendered a
page. This does, for english / hinglish / hindi.
"""
from conftest import maybe_await
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
    "admin_analytics", "admin_accounts", "admin_setup", "admin_pick",
])
def test_admin_pages_render(page, lang):
    fn = getattr(admin, page)
    resp = maybe_await(fn(token="tok", lang=lang))
    _ok(resp)


@pytest.mark.parametrize("lang", LANGS)
def test_party_page_renders(lang):
    resp = maybe_await(admin.admin_party(token="tok", client_id="c1", lang=lang))
    _ok(resp)


def test_manual_payment_enqueue_does_not_crash():
    # Regression: admin_promise_action used names.clean_display but `names` was not
    # imported at that scope -> NameError -> the manual "Enter payment / Post to
    # Tally" 500'd ("Could not save") while the WhatsApp PAID path worked. Exercise
    # the POST endpoint end to end (page-render tests never hit it).
    from app.routers.admin import PromiseActionPayload
    out = maybe_await(admin.admin_promise_action(
        PromiseActionPayload(token="tok", client_id="c1", action="enqueue",
                             amount=5000, deposit_ledger="INDIAN BANK",
                             receipt_date="2026-08-28")))
    assert out.get("ok") is True


def test_dashboard_collapses_to_cards_on_narrow():
    # P2: the 9-column parties table must carry per-cell labels + a narrow-width
    # media query so WhatsApp + Actions never scroll off-screen on a small laptop.
    text = _ok(maybe_await(admin.admin_page(token="tok", lang="hinglish")))
    assert "@media (max-width:760px)" in text
    assert 'data-label="Actions"' in text
    assert 'data-label="WhatsApp"' in text
    assert 'class="c-act"' in text


@pytest.mark.parametrize("lang", LANGS)
def test_today_has_language_runtime(lang):
    # The Today page must ship the TT() runtime so its JS-built strings localise.
    text = _ok(maybe_await(admin.admin_today(token="tok", lang=lang)))
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
    resp = maybe_await(admin.admin_page(token="tok", lang="english"))
    html = _ok(resp)
    bad = _re.findall(r"(?:&#8377;|₹|Rs\s?)\d{1,3},\d{3},\d{3}\b", html)
    assert not bad, "western-grouped rupee amounts leaked: %s" % bad[:5]


def test_no_python_literals_leak_into_dashboard_javascript():
    """A Python value embedded with repr() is not always a JS literal.

    `{flag!r}` on a bool emits `True`, which JS reads as an UNDEFINED IDENTIFIER:
    the whole <script> throws on load and the dashboard renders blank for every
    shop. node --check does not catch it (it is valid syntax, a runtime
    ReferenceError), so guard it here.
    """
    import re
    html = _ok(maybe_await(admin.admin_page(token="tok", lang="english")))
    js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    bad = re.findall(r"=\s*(True|False|None)\s*[;,)]", js)
    assert not bad, f"Python literals leaked into the dashboard JS: {set(bad)}"


def test_dashboard_exposes_setup_problem_and_party_state():
    # The dashboard must be able to tell "nothing to do" apart from "nothing
    # imported" - a shop that never imported a party must never read All clear.
    html = _ok(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert "const SETUP_PROBLEM =" in html
    assert "const HAS_PARTIES =" in html
    assert "No customers imported yet" in html


# ── The emitted JavaScript must actually parse ────────────────────────────
# Rendering 200 is not enough: these pages build their JS as Python strings, so
# a quoting slip (a lone \' inside a non-raw block emits a bare ') ends the JS
# string and kills the whole <script>. The page still returns 200 and still
# renders its skeleton, but nothing loads and no button works. That is exactly
# how Today stopped loading and the Payments "Add a payment" button went dead in
# 2.2.0. These tests parse every inline script with node, in every language,
# because the i18n pass rewrites text INSIDE those scripts too.
import html as _html
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile
from pathlib import Path as _Path

_NODE = _shutil.which("node")
_SCRIPT_RE = _re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", _re.S)


def _inline_scripts(page_text):
    return [_html.unescape(m.group(1)) for m in _SCRIPT_RE.finditer(page_text)
            if m.group(1).strip()]


def _js_parses(js):
    with _tempfile.TemporaryDirectory() as d:
        f = _Path(d) / "page.js"
        f.write_text(js, encoding="utf-8")
        r = _subprocess.run([_NODE, "--check", str(f)], capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "")[:500]


@pytest.mark.skipif(_NODE is None, reason="needs node to parse the emitted JS")
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("page", [
    "admin_page", "admin_today", "admin_payments", "admin_reminders",
    "admin_analytics", "admin_accounts", "admin_setup", "admin_pick",
])
def test_admin_page_javascript_parses(page, lang):
    text = _ok(maybe_await(getattr(admin, page)(token="tok", lang=lang)))
    scripts = _inline_scripts(text)
    for i, js in enumerate(scripts):
        ok, err = _js_parses(js)
        assert ok, "%s [%s] script #%d does not parse:\n%s" % (page, lang, i, err)


@pytest.mark.skipif(_NODE is None, reason="needs node to parse the emitted JS")
@pytest.mark.parametrize("lang", LANGS)
def test_party_page_javascript_parses(lang):
    text = _ok(maybe_await(admin.admin_party(token="tok", client_id="c1", lang=lang)))
    for i, js in enumerate(_inline_scripts(text)):
        ok, err = _js_parses(js)
        assert ok, "party [%s] script #%d does not parse:\n%s" % (lang, i, err)


def test_inline_onclick_quotes_are_escaped_for_javascript():
    """An onclick built inside a JS string needs a doubled backslash in the
    Python source; a single one emits a bare quote and breaks the script."""
    src = _Path("app/routers/admin.py").read_text(encoding="utf-8")
    bs = chr(92)
    single = "onclick=" + bs + "'"         # a lone backslash collapses to a bare quote
    good = "onclick=" + bs + bs + "'"      # what the JS actually needs
    bad = [ln.strip()[:160] for ln in src.split("\n")
           if "onclick='" in ln.replace(single, "")
           or (single in ln and good not in ln)]
    assert not bad, "unescaped quote in an inline onclick:\n" + "\n".join(bad[:5])


# ── Slow must not look broken ─────────────────────────────────────────────
@pytest.mark.parametrize("page", [
    "admin_page", "admin_today", "admin_payments", "admin_reminders",
    "admin_analytics", "admin_accounts", "admin_setup", "admin_pick",
])
def test_every_page_carries_the_responsiveness_layer(page):
    """A server-rendered page over a tunnel takes a second or three to come
    back. Without the progress bar and the spinner that reads as a hang, which
    is what made people press a slow button twice."""
    html = _ok(maybe_await(getattr(admin, page)(token="tok", lang="english")))
    # one bar, one listener: the layer guards against a second injection
    assert html.count("id = 'asva-prog'") == 1, "layer missing or injected twice"
    assert "__asvaFeedback" in html
    assert "asva-prog" in html and "asva-busy" in html


@pytest.mark.skipif(_NODE is None, reason="needs node to run the regex")
def test_the_rescue_only_frees_buttons_asva_itself_made_busy():
    """The 15-second rescue re-enables a button whose request died. It must
    match only the labels ASVA sets while waiting: a button the page disabled
    for its own reasons has to stay disabled."""
    import json
    import re as _re2
    html = _ok(maybe_await(admin.admin_page(token="tok", lang="english")))
    m = _re2.search(r"var BUSY = (/.*?/);", html)
    assert m, "the rescue regex is gone"
    script = (
        "const BUSY=" + m.group(1) + ";"
        "const busy=['...','Sending...','Saving...','Preparing...','Checking...'];"
        "const real=['Send now','Save','Add a payment','Exclude','Bring back','Sent','Done'];"
        "if(!busy.every(x=>BUSY.test(x))) { console.log('MISS'); process.exit(1); }"
        "if(real.some(x=>BUSY.test(x))) { console.log('OVERREACH'); process.exit(1); }"
        "console.log('OK');"
    )
    r = _subprocess.run([_NODE, "-e", script], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, (r.stdout + r.stderr)[:400]


def _pick_data(monkeypatch, n=12):
    """The pick page with a real shortlist on it: the stub DB returns no bills,
    so without this the only branch ever rendered is "nothing to pick"."""
    parties = [{**CLIENT, "id": f"c{i}", "name": f"Party <{i}> & Co",
                "reminders_enabled": False} for i in range(n)]
    bills = [{"client_id": f"c{i}", "outstanding": 1000 * (i + 1),
              "due_date": "2026-01-0%d" % ((i % 9) + 1),
              "invoice_date": "2025-12-01"} for i in range(n)]
    monkeypatch.setattr(admin, "_all_parties", lambda db, bid: parties)
    monkeypatch.setattr(admin, "_open_bill_rows", lambda db, bid: bills)


@pytest.mark.parametrize("lang", LANGS)
def test_pick_page_lists_the_shortlist(monkeypatch, lang):
    from app.services import shortlist
    _pick_data(monkeypatch)
    text = _ok(maybe_await(admin.admin_pick(token="tok", lang=lang, n=0)))
    assert text.count('class="prow"') == shortlist.STARTER   # ten, not the book
    # names are escaped: a party called "R & S <Traders>" must not inject markup
    assert "&lt;" in text and "&gt; &amp; Co" in text
    assert "<1>" not in text


@pytest.mark.skipif(_NODE is None, reason="needs node to parse the emitted JS")
def test_pick_page_javascript_parses(monkeypatch):
    _pick_data(monkeypatch)
    text = _ok(maybe_await(admin.admin_pick(token="tok", lang="english", n=0)))
    for i, js in enumerate(_inline_scripts(text)):
        ok, err = _js_parses(js)
        assert ok, "pick script #%d does not parse:\n%s" % (i, err)


def test_pick_page_can_ask_for_the_next_fifty(monkeypatch):
    from app.services import shortlist
    _pick_data(monkeypatch, n=80)
    text = _ok(maybe_await(admin.admin_pick(token="tok", lang="english",
                                            n=shortlist.NEXT_STEP)))
    assert text.count('class="prow"') == shortlist.NEXT_STEP


def test_pick_banner_asks_a_shop_that_is_chasing_nobody(monkeypatch):
    """The dashboard's one line about it. A shop with everything switched off
    was shown nothing at all, which is how 88 percent opt-out stayed invisible."""
    _pick_data(monkeypatch)
    text = _ok(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert "not reminding anybody yet" in text
    assert "/admin/pick?token=tok" in text
