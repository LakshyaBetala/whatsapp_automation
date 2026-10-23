"""The Command Center must RENDER and its data builder must never 500. Mirrors the
admin render-guard: a page/JSON that only fails at request time (never in a unit
test) is exactly how a 500 shipped green before. This renders /ops and exercises
build_ops_data on a fake DB so a broken template or query is caught in CI.
"""
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from conftest import ops_request
from app.routers import ops

BIZ = {
    "id": "b1", "business_name": "Test Traders", "plan": "starter",
    "plan_expires_on": "2026-09-30", "license_key": "ASVA-XXXX", "last_seen": None,
    "agent_version": "2.1.1", "whatsapp_number": "919000000000",
}


class _Resp:
    def __init__(self, data): self.data = data


class _Tbl:
    def __init__(self, name): self.name = name
    def __getattr__(self, _): return lambda *a, **k: self
    def execute(self):
        return _Resp([dict(BIZ)] if self.name == "businesses" else [])


class FakeDB:
    def table(self, name): return _Tbl(name)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(ops, "require_db", lambda: FakeDB())
    monkeypatch.setattr(ops.settings, "admin_api_key", "testkey")


def _body(resp):
    b = getattr(resp, "body", b"") or b""
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(resp)


def test_ops_page_renders_with_key():
    resp = asyncio.run(ops.ops_page(request=ops_request(), key=""))
    assert resp.status_code == 200
    html = _body(resp)
    assert "Internal Server Error" not in html
    # New controls/transparency elements are present.
    for needle in ('id="statuspill"', 'id="hfilter"', 'id="sfilter"',
                   'id="hprob"', 'id="sprob"', "applyHFilter", "applySFilter",
                   "setStatusPill", "sendCodeWA", "/ops/send-code",
                   'id="p_sendmsg"', 'id="a_sendmsg"'):
        assert needle in html, "missing %s" % needle


def test_ops_send_code_bad_key_refused():
    from fastapi import HTTPException
    try:
        asyncio.run(ops.ops_send_code(ops.SendCodePayload(admin_key="nope", business_id="b1")))
        assert False, "should refuse a bad key"
    except HTTPException as e:
        assert e.status_code == 401


def test_ops_page_wrong_key_shows_login_not_500():
    resp = asyncio.run(ops.ops_page(request=ops_request(signed_in=False), key="nope"))
    assert resp.status_code == 200
    assert "Command Center" in _body(resp)


def test_ops_page_disabled_without_admin_key(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", "")
    resp = asyncio.run(ops.ops_page(request=ops_request(signed_in=False), key="anything"))
    assert resp.status_code == 503


def test_build_ops_data_never_raises():
    data = ops.build_ops_data(FakeDB())
    assert "totals" in data and "businesses" in data
    t = data["totals"]
    # totals carry the KPI keys the page colours by.
    for k in ("businesses", "online", "active", "grace", "suspended",
              "failed_today", "outdated", "needs_reinstall"):
        assert k in t
    # JSON-serialisable (the /ops/data endpoint returns it as JSON).
    import json
    json.dumps(data, default=str)


def test_ops_page_javascript_parses(tmp_path):
    """The Command Center is one big inline <script>: a single syntax slip
    blanks the whole operator console. Parse it with node when available."""
    import re, shutil, subprocess
    html = _body(asyncio.run(ops.ops_page(request=ops_request(), key="")))
    js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    assert js.strip(), "no inline script found"
    # Python's repr() is not a JS literal - `True` is an undefined identifier
    # and throws before any of the console's code runs.
    assert not re.findall(r"=\s*(True|False|None)\s*[;,)]", js)
    node = shutil.which("node")
    if not node:                      # CI without node: the checks above still ran
        return
    f = tmp_path / "ops.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:800]


def test_relink_controls_are_present():
    # The bot QR page is no longer reachable in a browser (localhost bind +
    # WA_API_KEY), so re-linking MUST be possible from here.
    html = _body(asyncio.run(ops.ops_page(request=ops_request(), key="")))
    for needle in ('id="walink"', 'id="wabody"', 'id="warelink"',
                   "openWaLink", "doWaRelink", "/ops/wa-qr", "/ops/wa-relink"):
        assert needle in html, "missing %s" % needle


# ── The controls the business is run with ─────────────────────────────────
def test_every_billing_control_is_on_the_page():
    """Trial, month, year, exact date, cut off, pair, delete. Lose one of these
    and the operator is back to editing the database by hand."""
    html = _body(asyncio.run(ops.ops_page(request=ops_request(), key="")))
    for fn in ("startTrial(", "renew(", "renewYear(", "renewUntil(",
               "suspend(", "pairCode(", "delBiz("):
        assert fn in html, "the Command Center lost its %s control" % fn
    assert "/license/trial" in html, "no way to start a free trial"
    assert "months:12" in html, "no annual renewal"


def test_the_money_line_is_shown():
    html = _body(asyncio.run(ops.ops_page(request=ops_request(), key="")))
    for label in ("Monthly, paying", "Renew in 7 days", "On free trial"):
        assert label in html, "missing from the Subscriptions header: " + label


def test_totals_separate_paying_shops_from_trials():
    """A trial shop is live but pays nothing. Counting it as revenue is the
    one number an operator must never be lied to about."""
    import datetime as _d

    def _db(row):
        class _T(_Tbl):
            def execute(self):
                return _Resp([row] if self.name == "businesses" else [])

        class _DB:
            def table(self, name):
                return _T(name)
        return _DB()

    soon = (_d.date.today() + _d.timedelta(days=3)).isoformat()
    trial = dict(BIZ, subscription_status="trial", plan="pro", plan_expires_on=soon)
    t = ops.build_ops_data(_db(trial))["totals"]
    assert t["trials"] == 1 and t["mrr"] == 0 and t["renew_7"] == 0

    paid = dict(BIZ, subscription_status="active", plan="pro", plan_expires_on=soon)
    out = ops.build_ops_data(_db(paid))
    t = out["totals"]
    assert t["mrr"] == 1499, "a paying Recover shop is Rs 1,499 a month"
    assert t["renew_7"] == 1 and t["trials"] == 0
    assert out["businesses"][0]["on_trial"] is False
