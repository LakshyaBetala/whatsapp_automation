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


def test_internal_route_hides_from_the_public(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(wa_server.settings, "wa_api_key", "secret")
    c = TestClient(app)
    # TestClient's client host is "testclient", i.e. not localhost -> 404
    assert c.get("/internal/wa-shops", headers={"x-asva-key": "secret"}).status_code == 404


# ── owner page: start, then QR, then connected ────────────────────────────
def _admin(monkeypatch, biz, child_status):
    from app.routers import admin
    rec = {"requested": [], "connected": []}
    monkeypatch.setattr(admin, "_biz_by_token", lambda token: dict(biz))
    monkeypatch.setattr(admin, "require_db", lambda: object())
    monkeypatch.setattr(admin.wa_server, "request_server",
                        lambda db, bid: rec["requested"].append(bid) or 4101)
    monkeypatch.setattr(admin.wa_server, "status_sync", lambda port: child_status)
    monkeypatch.setattr(admin.wa_server, "mark_connected",
                        lambda db, bid, already_linked: rec["connected"].append(bid))
    return admin, rec


BIZ = {"id": "b1", "business_name": "Ram Traders", "wa_transport": "laptop",
       "wa_port": None, "wa_server_requested_at": None, "wa_linked_at": None}


def test_status_before_start(monkeypatch):
    admin, _ = _admin(monkeypatch, BIZ, None)
    assert admin.admin_whatsapp_status(token="t")["phase"] == "not_started"


def test_start_requests_a_server_process(monkeypatch):
    admin, rec = _admin(monkeypatch, BIZ, None)
    out = admin.admin_whatsapp_start(admin.TokenPayload(token="t"))
    assert out["ok"] is True and rec["requested"] == ["b1"]


def test_status_shows_qr_then_connects(monkeypatch):
    started = dict(BIZ, wa_port=4101, wa_server_requested_at="x")
    admin, rec = _admin(monkeypatch, started, {"ready": False, "qr": "data:image/png;base64,AAA"})
    st = admin.admin_whatsapp_status(token="t")
    assert st["phase"] == "qr" and st["qr"].startswith("data:image/png")
    admin, rec = _admin(monkeypatch, started, {"ready": True, "qr": None})
    st = admin.admin_whatsapp_status(token="t")
    assert st["phase"] == "connected" and rec["connected"] == ["b1"]


def test_page_renders_plain_words_without_dashes(monkeypatch):
    admin, _ = _admin(monkeypatch, BIZ, None)
    html = admin.admin_whatsapp(token="t", lang="english").body.decode()
    assert "even when this computer is off" in html and "Linked devices" in html
    assert "—" not in html and "–" not in html


def test_whatsapp_page_script_is_valid_javascript(monkeypatch, tmp_path):
    import re
    import shutil
    import subprocess
    admin, _ = _admin(monkeypatch, BIZ, None)
    html = admin.admin_whatsapp(token="t'x", lang="english").body.decode()
    js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    assert "const TOKEN = \"t'x\";" in js          # json.dumps, not repr
    node = shutil.which("node")
    if not node:
        return
    f = tmp_path / "wa.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:600]
