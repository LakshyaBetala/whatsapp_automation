"""ASVA Command Center (ops): the operator health + subscription cockpit.

Tests the data builder (totals, status, online flag, problem-first sort) with a
tiny fake DB, and the admin-key gates on the ops actions.
"""
import asyncio
import datetime as _dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.routers import ops, license as lr


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _DB:
    def __init__(self, tables):
        self.t = tables

    def table(self, name):
        return _Q(self.t.get(name, []))


def _iso(mins_ago):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=mins_ago)).isoformat()


def test_build_ops_data_totals_status_online_sort():
    today = _dt.date.today()
    active_exp = (today + _dt.timedelta(days=20)).isoformat()
    suspended_exp = (today - _dt.timedelta(days=30)).isoformat()
    db = _DB({
        "businesses": [
            {"id": "a", "business_name": "Alpha", "plan": "pro",
             "plan_expires_on": active_exp, "license_key": "ASVA-A",
             "last_seen": _iso(1), "agent_version": "1.3.0", "whatsapp_number": "9"},
            {"id": "b", "business_name": "Beta", "plan": "starter",
             "plan_expires_on": suspended_exp, "license_key": "ASVA-B",
             "last_seen": _iso(600), "agent_version": "1.0.0", "whatsapp_number": "9"},
        ],
        "usage": [{"business_id": "a", "message_count": 100},
                  {"business_id": "b", "message_count": 5}],
        "messages": [{"business_id": "a"}, {"business_id": "a"}],  # 2 failed today
        "app_releases": [{"version": "1.3.0", "mandatory": False}],
    })
    d = ops.build_ops_data(db)

    t = d["totals"]
    assert t["businesses"] == 2
    assert t["active"] == 1 and t["suspended"] == 1
    assert t["online"] == 1                 # only Alpha (seen 1 min ago)
    assert t["messages_month"] == 105
    assert t["failed_today"] == 2
    assert t["outdated"] == 1               # Beta on 1.0.0 vs latest 1.3.0

    # problem-first sort: suspended Beta before active Alpha
    assert [r["name"] for r in d["businesses"]] == ["Beta", "Alpha"]
    beta, alpha = d["businesses"]
    assert beta["status"] == "suspended" and alpha["status"] == "active"
    assert alpha["online"] is True and beta["online"] is False
    assert alpha["version_ok"] is True and beta["version_ok"] is False
    assert alpha["messages_limit"] == 8000 and beta["messages_limit"] == 2400


def test_ops_data_requires_key(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", "K")
    from fastapi import HTTPException
    try:
        asyncio.run(ops.ops_data(key="wrong"))
        assert False
    except HTTPException as e:
        assert e.status_code == 401


def test_ops_page_disabled_without_key(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", "")
    resp = asyncio.run(ops.ops_page(key=""))
    assert resp.status_code == 503


def test_set_plan_and_suspend_admin_gate(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(lr.settings, "admin_api_key", "K")
    for call in (
        lambda: lr.set_plan(lr.SetPlanPayload(admin_key="bad", business_id="b", plan="pro")),
        lambda: lr.suspend(lr.SuspendPayload(admin_key="bad", business_id="b")),
    ):
        try:
            asyncio.run(call())
            assert False
        except HTTPException as e:
            assert e.status_code == 401
