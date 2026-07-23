"""End-to-end integration + authorization tests, driven through real HTTP.

Black box: every request below goes through the actual FastAPI app (routing,
auth, validation, serialization). Nothing calls a service function directly.

Two things are proved here:
  1. The onboarding journey works start to finish: create a business, get a
     pairing code, redeem it on a "fresh install", receive the agent_token.
  2. The authorization matrix holds: admin actions need the admin key, the
     pairing code is single-use, the secret-bearing zip stays gated while the
     secret-free installer is public.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from fastapi.testclient import TestClient

from app.main import app
from app.routers import downloads as dl_mod
from app.routers import license as lic_mod
from app.routers import ops as ops_mod

ADMIN = "integration-admin-key"


# ── A Supabase-shaped fake good enough for the whole onboarding path ──────
class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.rows = store, name, store[name]
        self._op = self._payload = self._limit = None
        self._filters = []

    def insert(self, row):
        self._op, self._payload = "insert", dict(row); return self

    def update(self, patch):
        self._op, self._payload = "update", dict(patch); return self

    def select(self, *a, **k):
        self._op = "select"; return self

    def eq(self, f, v):
        self._filters.append(("eq", f, v)); return self

    def is_(self, f, v):
        self._filters.append(("is", f, v)); return self

    def in_(self, f, v):
        self._filters.append(("in", f, v)); return self

    def neq(self, f, v):
        self._filters.append(("neq", f, v)); return self

    def gt(self, f, v):
        self._filters.append(("gt", f, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n; return self

    def single(self):
        self._limit = 1; return self

    def range(self, a, b):
        self._limit = b - a + 1; return self

    def _match(self, r):
        for op, f, v in self._filters:
            if op == "eq" and r.get(f) != v:
                return False
            if op == "neq" and r.get(f) == v:
                return False
            if op == "in" and r.get(f) not in v:
                return False
            if op == "gt" and not (r.get(f) is not None and str(r.get(f)) > str(v)):
                return False
            if op == "is" and v == "null" and r.get(f) is not None:
                return False
        return True

    def execute(self):
        R = lambda data: type("R", (), {"data": data})()
        if self._op == "insert":
            row = self._payload
            if self.name == "businesses":
                row.setdefault("id", f"biz-{len(self.rows) + 1}")
            if self.name == "pairing_codes" and any(
                    r["code"] == row["code"] for r in self.rows):
                raise Exception("duplicate code")
            self.rows.append(row)
            return R([row])
        if self._op == "update":
            hit = [r for r in self.rows if self._match(r)]
            for r in hit:
                r.update(self._payload)
            return R(hit)
        out = [dict(r) for r in self.rows if self._match(r)]
        if self._limit is not None:
            out = out[:self._limit]
        return R(out)


class FakeDB:
    def __init__(self):
        self.store = {"businesses": [], "pairing_codes": [], "usage": [],
                      "bills": [], "clients": [], "app_releases": []}

    def table(self, name):
        self.store.setdefault(name, [])
        return _Q(self.store, name)


@pytest.fixture
def client(monkeypatch):
    db = FakeDB()
    async def _noop_welcome(*a, **k):   # don't hit real WhatsApp/DB in tests
        return None
    monkeypatch.setattr(lic_mod, "_send_welcome", _noop_welcome)
    monkeypatch.setattr(lic_mod, "require_db", lambda: db)
    monkeypatch.setattr(ops_mod, "require_db", lambda: db)
    monkeypatch.setattr(dl_mod, "get_client", lambda: db)
    monkeypatch.setattr(lic_mod.settings, "admin_api_key", ADMIN)
    monkeypatch.setattr(ops_mod.settings, "admin_api_key", ADMIN)
    monkeypatch.setattr(dl_mod.settings, "admin_api_key", ADMIN)
    c = TestClient(app)          # no context manager: don't boot the scheduler
    c._db = db
    return c


# ── THE JOURNEY: onboard a shop, pair a fresh install ─────────────────────
def test_full_onboarding_journey(client):
    # 1. Operator creates the business (admin-gated).
    r = client.post("/license/create-business", json={
        "admin_key": ADMIN, "owner_name": "Papa",
        "business_name": "RISHAB TRADING COMPANY",
        "whatsapp_number": "9444294894", "plan": "pro", "months": 1})
    assert r.status_code == 200, r.text
    created = r.json()
    business_id = created["business_id"]
    secret_token = created["agent_token"]

    # 2. The operator gets a short code to read aloud - THIS is onboarding.
    code = created["pairing_code"]
    assert code and len(code) == 8
    assert created["pairing_code_display"][4] == "-"      # K7P2-9M4T
    assert not set(code) & set("01OIL")                   # no confusable chars

    # 3. A fresh install redeems it and receives its identity.
    r = client.post("/license/pair", json={"code": code})
    assert r.status_code == 200, r.text
    paired = r.json()
    assert paired["business_id"] == business_id
    assert paired["agent_token"] == secret_token          # same shop, same token
    assert paired["business_name"] == "RISHAB TRADING COMPANY"

    # 4. That code is now burnt - a replay cannot steal the token.
    r = client.post("/license/pair", json={"code": code})
    assert r.status_code == 400
    assert "already used" in r.json()["detail"].lower()

    # 5. The paired install can authenticate for real work.
    r = client.get(f"/license/status?token={secret_token}")
    assert r.status_code == 200
    assert r.json()["business_id"] == business_id


def test_repair_existing_business_keeps_the_same_identity(client):
    """The cutover guarantee: a re-pair code returns the SAME business_id and
    token, so DB-stored reminders carry over to the new install."""
    r = client.post("/license/create-business", json={
        "admin_key": ADMIN, "owner_name": "Papa", "business_name": "RISHAB",
        "whatsapp_number": "9444294894"})
    first = r.json()

    r = client.post("/license/mint-code", json={
        "admin_key": ADMIN, "business_id": first["business_id"]})
    assert r.status_code == 200
    recode = r.json()["code"]
    # Idempotent Get-code: while the first code is still valid and unused, "Get
    # code" hands back the SAME one rather than piling up new codes.
    assert recode == first["pairing_code"]

    r = client.post("/license/pair", json={"code": recode})
    assert r.status_code == 200
    assert r.json()["business_id"] == first["business_id"]
    assert r.json()["agent_token"] == first["agent_token"]


# ── AUTHORIZATION MATRIX ──────────────────────────────────────────────────
@pytest.mark.parametrize("path,body", [
    ("/license/create-business",
     {"admin_key": "WRONG", "owner_name": "x", "whatsapp_number": "9444294894"}),
    ("/license/mint-code", {"admin_key": "WRONG", "business_id": "b1"}),
    ("/license/renew", {"admin_key": "WRONG", "business_id": "b1", "months": 1}),
    ("/license/set-plan", {"admin_key": "WRONG", "business_id": "b1", "plan": "pro"}),
    ("/license/suspend", {"admin_key": "WRONG", "business_id": "b1"}),
])
def test_admin_endpoints_reject_a_wrong_key(client, path, body):
    assert client.post(path, json=body).status_code == 401


def test_ops_data_and_health_require_the_admin_key(client):
    assert client.get("/ops/data").status_code == 401
    assert client.get("/ops/data?key=WRONG").status_code == 401
    assert client.get("/ops/health?key=WRONG").status_code == 401
    # The page itself renders a password prompt rather than leaking data.
    page = client.get("/ops")
    assert page.status_code == 200
    assert "agent_token" not in page.text


def test_pairing_rejects_garbage_codes(client):
    for bad in ["", "1234", "ZZZZ9999", "!!!!!!!!", "K7P2-9M4T-EXTRA"]:
        r = client.post("/license/pair", json={"code": bad})
        assert r.status_code == 400, f"{bad!r} should be refused"


def test_status_requires_a_real_agent_token(client):
    assert client.get("/license/status?token=not-a-real-token").status_code == 401


# ── DOWNLOADS: the installer is public, the key-bearing zip is not ────────
def test_installer_is_public_but_zip_is_gated(client, monkeypatch):
    # File not published in this test env -> 404 proves auth was not the blocker.
    monkeypatch.setattr(dl_mod.os.path, "exists", lambda p: False)
    assert client.get("/download/ASVA-Setup.exe").status_code == 404
    # The zip still demands a token.
    assert client.get("/download/ASVA_shop.zip").status_code == 403


def test_download_path_traversal_is_refused(client):
    for name in ["../.env", "..%2F.env", "secrets.env", "ASVA_shop.zip/../.env"]:
        assert client.get(f"/download/{name}").status_code in (403, 404)


# ── Liveness ──────────────────────────────────────────────────────────────
def test_health_is_public_and_leaks_nothing_sensitive(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.text.lower()
    for secret in ("service_role", "agent_token", "admin", "password", "key="):
        assert secret not in body
