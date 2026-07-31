"""Pairing codes (migration 026): passwordless onboarding for the thin client.

The operator mints a single-use code bound to a business; the shop's installer
redeems it and receives the agent_token - no token is ever typed by hand. These
tests cover mint/redeem, single-use, expiry, the unambiguous alphabet, and the
admin gate on /license/mint-code plus the public /license/pair error mapping.
"""
import asyncio
import datetime as _dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import pairing


# ── A tiny Supabase-shaped fake supporting the exact chains pairing uses ──
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

    def limit(self, n):
        self._limit = n; return self

    def order(self, *a, **k):
        return self

    def _match(self, r):
        for op, f, v in self._filters:
            if op == "eq" and r.get(f) != v:
                return False
            if op == "is" and v == "null" and r.get(f) is not None:
                return False
        return True

    def execute(self):
        R = lambda data: type("R", (), {"data": data})()
        if self._op == "insert":
            if self.name == "pairing_codes" and any(
                    r["code"] == self._payload["code"] for r in self.rows):
                raise Exception("duplicate code")     # exercise mint's retry
            self.rows.append(self._payload)
            return R([self._payload])
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
        self.store = {"pairing_codes": [], "businesses": []}

    def table(self, name):
        self.store.setdefault(name, [])
        return _Q(self.store, name)

    def add_business(self, bid="b1", name="RISHAB TRADING COMPANY",
                     token="tok-secret-123"):
        self.store["businesses"].append(
            {"id": bid, "business_name": name, "agent_token": token,
             "license_key": "ASVA-AAAA-BBBB-CCCC"})


# ── code shape ────────────────────────────────────────────────────────────
def test_code_alphabet_is_unambiguous():
    for _ in range(200):
        c = pairing.generate_code()
        assert len(c) == 8
        assert all(ch not in "01OIL" for ch in c)   # no confusable characters


def test_normalize_and_format_roundtrip():
    assert pairing.normalize_code(" k7p2-9m4t ") == "K7P29M4T"
    assert pairing.format_code("K7P29M4T") == "K7P2-9M4T"
    # a code typed with the dash still redeems (normalize strips it)
    assert pairing.normalize_code("K7P2-9M4T") == "K7P29M4T"


# ── mint + redeem happy path ───────────────────────────────────────────────
def test_mint_then_redeem_returns_token_and_consumes_code():
    db = FakeDB(); db.add_business()
    minted = pairing.mint(db, "b1", note="new-business")
    assert minted["code_display"][4] == "-"

    out = pairing.redeem(db, minted["code_display"])   # dashed form still works
    assert out["business_id"] == "b1"
    assert out["agent_token"] == "tok-secret-123"
    assert out["business_name"] == "RISHAB TRADING COMPANY"
    # code is now marked used
    assert db.store["pairing_codes"][0]["used_at"] is not None


def test_redeem_is_single_use():
    db = FakeDB(); db.add_business()
    code = pairing.mint(db, "b1")["code"]
    pairing.redeem(db, code)
    try:
        pairing.redeem(db, code)
        assert False, "second redeem should fail"
    except pairing.PairingError as e:
        assert "already used" in str(e).lower()


def test_redeem_unknown_code():
    db = FakeDB(); db.add_business()
    try:
        pairing.redeem(db, "ZZZZ9999")
        assert False
    except pairing.PairingError as e:
        assert "not valid" in str(e).lower()


def test_redeem_expired_code():
    db = FakeDB(); db.add_business()
    past = (pairing._now() - _dt.timedelta(hours=1)).isoformat()
    db.store["pairing_codes"].append(
        {"code": "K7P29M4T", "business_id": "b1", "note": None,
         "expires_at": past, "used_at": None})
    try:
        pairing.redeem(db, "K7P29M4T")
        assert False
    except pairing.PairingError as e:
        assert "expired" in str(e).lower()


def test_redeem_rejects_malformed_length():
    db = FakeDB(); db.add_business()
    try:
        pairing.redeem(db, "K7P2")          # too short
        assert False
    except pairing.PairingError:
        pass


# ── router: admin gate on mint-code, public error mapping on /pair ─────────
def test_mint_code_endpoint_admin_gate(monkeypatch):
    from app.routers import license as lr
    from fastapi import HTTPException
    monkeypatch.setattr(lr.settings, "admin_api_key", "s3cret")
    try:
        asyncio.run(lr.mint_code(lr.MintCodePayload(
            admin_key="wrong", business_id="b1")))
        assert False, "should refuse a bad admin key"
    except HTTPException as e:
        assert e.status_code == 401


def test_pair_endpoint_maps_bad_code_to_400(monkeypatch):
    from app.routers import license as lr
    from fastapi import HTTPException
    db = FakeDB(); db.add_business()
    monkeypatch.setattr(lr, "require_db", lambda: db)
    try:
        asyncio.run(lr.pair(lr.PairPayload(code="ZZZZ9999")))
        assert False, "unknown code should 400"
    except HTTPException as e:
        assert e.status_code == 400

    # a freshly minted code pairs successfully through the endpoint
    code = pairing.mint(db, "b1")["code"]
    res = asyncio.run(lr.pair(lr.PairPayload(code=code)))
    assert res["ok"] and res["agent_token"] == "tok-secret-123"
