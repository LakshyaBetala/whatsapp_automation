"""Leaving the ASVA server must be a real exit, and must never cost data.

Moving a shop's WhatsApp to the server is one button, so leaving is too. The
rules pinned here: the number is logged out (ASVA disappears from the phone's
Linked devices), the shop drops off the manager's list so its process stops,
sending returns to the shop's own computer, and a child that cannot be reached
does not trap the owner on the server.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import wa_server


class _Tbl:
    def __init__(self, state):
        self.state = state
        self._patch = None

    def update(self, row):
        self._patch = dict(row)
        return self

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        if self._patch is not None:
            self.state["patch"] = self._patch
        return MagicMock(data=[])


class _DB:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _Tbl(self.state)


def test_leaving_logs_out_stops_the_process_and_returns_sending(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        called["url"] = url
        return _Resp()

    monkeypatch.setattr(wa_server.httpx, "post", _post)
    state: dict = {}
    ok = wa_server.release_server(_DB(state), "biz-1", 4101)

    assert ok is True
    assert called["url"].endswith("/api/wa/logout"), "the number must actually be logged out"
    patch = state["patch"]
    # dropping off the manager's list is what stops the shop's process
    assert patch["wa_server_requested_at"] is None
    # and sending goes back to the shop's own computer
    assert patch["wa_transport"] == "laptop"
    assert patch["wa_ready"] is False


def test_a_child_that_cannot_be_reached_does_not_trap_the_owner(monkeypatch):
    def _boom(url, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(wa_server.httpx, "post", _boom)
    state: dict = {}
    ok = wa_server.release_server(_DB(state), "biz-1", 4101)

    assert ok is False                      # honest: the logout was not confirmed
    assert state["patch"]["wa_transport"] == "laptop"
    assert state["patch"]["wa_server_requested_at"] is None


def test_a_shop_with_no_port_still_leaves_cleanly(monkeypatch):
    monkeypatch.setattr(wa_server.httpx, "post",
                        lambda *a, **k: pytest.fail("there is no child to call"))
    state: dict = {}
    assert wa_server.release_server(_DB(state), "biz-1", None) is False
    assert state["patch"]["wa_transport"] == "laptop"


def test_a_released_shop_is_no_longer_given_to_the_manager():
    """shops_for_manager skips a shop whose request flag was cleared, which is
    how the manager learns to stop that shop's process."""
    rows = [{"id": "biz-1", "wa_port": 4101, "agent_token": "t",
             "plan_expires_on": "2099-01-01", "wa_server_requested_at": None}]

    class _R:
        data = rows

    class _T:
        def select(self, *_a, **_k):
            return self

        def execute(self):
            return _R()

    class _D:
        def table(self, _n):
            return _T()

    assert wa_server.shops_for_manager(_D()) == []
