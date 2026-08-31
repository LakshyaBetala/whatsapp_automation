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
    resp = asyncio.run(ops.ops_page(key="testkey"))
    assert resp.status_code == 200
    html = _body(resp)
    assert "Internal Server Error" not in html
    # New controls/transparency elements are present.
    for needle in ('id="statuspill"', 'id="hfilter"', 'id="sfilter"',
                   'id="hprob"', 'id="sprob"', "applyHFilter", "applySFilter",
                   "setStatusPill"):
        assert needle in html, "missing %s" % needle


def test_ops_page_wrong_key_shows_login_not_500():
    resp = asyncio.run(ops.ops_page(key="nope"))
    assert resp.status_code == 200
    assert "Command Center" in _body(resp)


def test_ops_page_disabled_without_admin_key(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", "")
    resp = asyncio.run(ops.ops_page(key="anything"))
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
