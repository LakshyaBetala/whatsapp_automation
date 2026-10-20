"""Shop WhatsApp on the server: ports, phases, the transport switch, the
manager's shop list, and the internal endpoint's guard."""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import wa_server


def test_allocate_port_takes_the_first_free_one():
    assert wa_server.allocate_port(set(), 4101) == 4101
    assert wa_server.allocate_port({4101, 4102, 4104}, 4101) == 4103


def test_phase_from_child_status():
    assert wa_server.phase(None) == "starting"
    assert wa_server.phase({"ready": False, "qr": None}) == "starting"
    assert wa_server.phase({"ready": False, "qr": "data:image/png;base64,x"}) == "qr"
    assert wa_server.phase({"ready": True, "qr": None}) == "connected"


class _Q:
    def __init__(self, db, name): self.db, self.name, self.patch, self.filters = db, name, None, []
    def select(self, *a): return self
    def update(self, patch): self.patch = patch; return self
    def eq(self, k, v): self.filters.append((k, v)); return self
    def not_(self): return self
    @property
    def is_(self): return lambda *a: self
    def limit(self, *a): return self
    def execute(self):
        if self.patch is not None:
            self.db.updates.append((self.name, self.patch, self.filters)); return type("R", (), {"data": [self.patch]})()
        return type("R", (), {"data": self.db.rows})()


class DB:
    def __init__(self, rows): self.rows, self.updates = rows, []
    def table(self, name): return _Q(self, name)


def test_request_server_assigns_a_free_port_once():
    db = DB([{"id": "b1", "wa_port": None, "wa_server_requested_at": None},
             {"id": "b2", "wa_port": 4101, "wa_server_requested_at": "x"}])
    port = wa_server.request_server(db, "b1")
    assert port == 4102
    name, patch, filters = db.updates[-1]
    assert patch["wa_port"] == 4102 and patch["wa_server_requested_at"] and ("id", "b1") in filters


def test_mark_connected_flips_transport_and_stamps_link_once():
    db = DB([])
    wa_server.mark_connected(db, "b1", already_linked=False)
    patch = db.updates[-1][1]
    assert patch["wa_transport"] == "server" and patch["wa_ready"] is True and patch["wa_linked_at"]
    wa_server.mark_connected(db, "b1", already_linked=True)
    assert "wa_linked_at" not in db.updates[-1][1]


def test_manager_list_skips_suspended_shops(monkeypatch):
    monkeypatch.setattr(wa_server.subs, "live_status", lambda exp: "suspended" if exp == "old" else "active")
    db = DB([{"id": "b1", "wa_port": 4101, "agent_token": "t1", "plan_expires_on": "2099-01-01"},
             {"id": "b2", "wa_port": 4102, "agent_token": "t2", "plan_expires_on": "old"}])
    assert wa_server.shops_for_manager(db) == [{"business_id": "b1", "port": 4101, "token": "t1"}]


def test_internal_guard_needs_localhost_key_and_no_tunnel_headers(monkeypatch):
    monkeypatch.setattr(wa_server.settings, "wa_api_key", "secret")
    ok = wa_server.manager_request_allowed
    assert ok("127.0.0.1", {"x-asva-key": "secret"}) is True
    assert ok("127.0.0.1", {"x-asva-key": "wrong"}) is False
    assert ok("10.0.0.5", {"x-asva-key": "secret"}) is False
    # Cloudflare tunnel traffic also arrives from 127.0.0.1 - its headers give it away.
    assert ok("127.0.0.1", {"x-asva-key": "secret", "cf-connecting-ip": "1.2.3.4"}) is False
    monkeypatch.setattr(wa_server.settings, "wa_api_key", "")
    assert ok("127.0.0.1", {"x-asva-key": ""}) is False
