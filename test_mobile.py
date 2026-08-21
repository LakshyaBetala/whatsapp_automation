"""Read-only mobile companion (app/routers/mobile.py).

The whole promise of the phone app is "you cannot change anything from here", so
the first test proves that structurally: every route is GET. The rest check auth,
the who-to-chase logic (excluded out, promised paused), and the party view.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers import mobile
from app.services import promises


# ── a tiny fake Supabase ────────────────────────────────────────────────────
class _Q:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []

    def select(self, *a, **k): return self
    def eq(self, f, v): self._filters.append((f, "eq", v)); return self
    def in_(self, f, v): self._filters.append((f, "in", list(v))); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._n = n; return self

    def _match(self, r):
        for f, op, v in self._filters:
            if op == "eq" and r.get(f) != v:
                return False
            if op == "in" and r.get(f) not in v:
                return False
        return True

    def execute(self):
        out = [dict(r) for r in self.rows if self._match(r)]
        n = getattr(self, "_n", None)
        if n is not None:
            out = out[:n]
        return type("R", (), {"data": out})()


class FakeDB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


BIZ = {"id": "biz1", "business_name": "RISHAB TRADING", "agent_token": "tok-good"}


def _db(clients, bills):
    return FakeDB({"businesses": [BIZ], "clients": clients, "bills": bills})


@pytest.fixture(autouse=True)
def _no_holds(monkeypatch):
    # default: nobody is on a promise hold (tests opt in)
    monkeypatch.setattr(promises, "held_now", lambda db, bids: {})
    monkeypatch.setattr(promises, "find_open", lambda db, bid, cid: None)


# ── the read-only guarantee ─────────────────────────────────────────────────
# The phone app is read-only EXCEPT two owner actions the owner explicitly
# triggers - send this reminder now, and record a payment - both auth-gated and
# reusing the desktop endpoints. No other mobile route may write.
_MOBILE_WRITE_ALLOWED = {"/m/api/remind", "/m/api/record-payment"}


def test_only_the_two_action_routes_write():
    for route in mobile.router.routes:
        methods = getattr(route, "methods", set()) or set()
        writes = methods & {"POST", "PUT", "PATCH", "DELETE"}
        if writes:
            assert route.path in _MOBILE_WRITE_ALLOWED, \
                f"{route.path} exposes {writes} - not an allowed mobile action"
            assert methods <= {"POST"}, f"{route.path} must be POST-only"


# ── auth ────────────────────────────────────────────────────────────────────
def test_summary_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(mobile, "require_db", lambda: _db([], []))
    with pytest.raises(HTTPException) as e:
        mobile._biz("")
    assert e.value.status_code == 401


def test_summary_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(mobile, "require_db", lambda: _db([], []))
    with pytest.raises(HTTPException) as e:
        mobile._biz("tok-wrong")
    assert e.value.status_code == 401


def test_good_token_resolves_business(monkeypatch):
    monkeypatch.setattr(mobile, "require_db", lambda: _db([], []))
    assert mobile._biz("tok-good")["id"] == "biz1"


def test_action_endpoints_reject_bad_token(monkeypatch):
    import asyncio
    monkeypatch.setattr(mobile, "require_db", lambda: _db([], []))
    with pytest.raises(HTTPException) as e:
        asyncio.run(mobile.api_remind(mobile._MRemind(token="nope", party="X")))
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        asyncio.run(mobile.api_record_payment(
            mobile._MPay(token="nope", client_id="c1", amount=100)))
    assert e.value.status_code == 401


# ── who to chase ────────────────────────────────────────────────────────────
def _clients():
    return [
        {"id": "c1", "business_id": "biz1", "name": "Ramesh", "excluded": False},
        {"id": "c2", "business_id": "biz1", "name": "Suresh", "excluded": False},
        {"id": "c3", "business_id": "biz1", "name": "Excluded Co", "excluded": True},
        {"id": "c4", "business_id": "biz1", "name": "No dues", "excluded": False},
    ]


def _bills():
    return [
        {"client_id": "c1", "business_id": "biz1", "outstanding": 5000, "due_date": "2000-01-01", "status": "overdue"},
        {"client_id": "c2", "business_id": "biz1", "outstanding": 3000, "due_date": "2000-01-01", "status": "pending"},
        {"client_id": "c3", "business_id": "biz1", "outstanding": 9000, "due_date": "2000-01-01", "status": "overdue"},
        {"client_id": "c4", "business_id": "biz1", "outstanding": 0, "due_date": "2000-01-01", "status": "pending"},
    ]


def test_summary_totals_and_excludes_the_excluded_party():
    d = mobile._build_summary(_db(_clients(), _bills()), BIZ)
    assert d["parties_owing"] == 2                    # c1 + c2 (c3 excluded, c4 zero)
    assert d["total_outstanding"] == "8,000"         # 5000 + 3000, not c3's 9000
    names = [p["name"] for p in d["chase"]]
    assert "Excluded Co" not in names and "No dues" not in names
    assert names == ["Ramesh", "Suresh"]             # most overdue/biggest first


def test_a_promised_party_is_counted_and_flagged_paused_but_sorted_last(monkeypatch):
    # The list now carries EVERY owing party (so the phone can search/open any of
    # them), but a paused/on-promise party is flagged and sorted after the active
    # ones - the default "who to chase" view hides paused parties client-side.
    monkeypatch.setattr(promises, "held_now", lambda db, bids: {"biz1": {"c1"}})
    d = mobile._build_summary(_db(_clients(), _bills()), BIZ)
    assert d["on_promise"] == 1
    by_name = {p["name"]: p for p in d["chase"]}
    assert by_name["Ramesh"]["paused"] is True and by_name["Suresh"]["paused"] is False
    # active party first, paused party last
    assert [p["name"] for p in d["chase"]] == ["Suresh", "Ramesh"]


# ── party detail ────────────────────────────────────────────────────────────
def test_party_detail_shows_open_bills_and_outstanding():
    db = FakeDB({"businesses": [BIZ],
                 "clients": [{"id": "c1", "business_id": "biz1", "name": "Ramesh", "excluded": False,
                              "reminders_enabled": True, "whatsapp_number": "919812345678"}],
                 "bills": [{"client_id": "c1", "business_id": "biz1", "invoice_number": "INV1", "outstanding": 5000,
                            "amount": 5000, "due_date": "2000-01-01", "invoice_date": "1999-12-01",
                            "status": "overdue"}]})
    d = mobile._build_party(db, BIZ, "c1")
    assert d["name"] == "Ramesh"
    assert d["outstanding"] == "5,000"
    assert len(d["open_bills"]) == 1 and d["open_bills"][0]["invoice"] == "INV1"
    assert d["promise"] is None


def test_party_detail_surfaces_the_promise(monkeypatch):
    monkeypatch.setattr(promises, "find_open", lambda db, bid, cid: {
        "kind": "promise", "promise_date": "2030-08-10", "hold_until": "2030-08-10T00:00:00Z",
        "raw_text": "10 tareek ko de dunga", "created_at": "2030-08-01T00:00:00Z"})
    db = FakeDB({"businesses": [BIZ],
                 "clients": [{"id": "c1", "business_id": "biz1", "name": "Ramesh", "reminders_enabled": True}],
                 "bills": []})
    d = mobile._build_party(db, BIZ, "c1")
    assert d["promise"]["said"] == "10 tareek ko de dunga"
    assert d["promise"]["promise_date"] == "2030-08-10"


def test_party_not_found_is_404():
    db = FakeDB({"businesses": [BIZ], "clients": [], "bills": []})
    with pytest.raises(HTTPException) as e:
        mobile._build_party(db, BIZ, "nope")
    assert e.value.status_code == 404


# ── end-to-end via a real HTTP client (the PWA shell + API served for real) ──
def _client(monkeypatch, db):
    monkeypatch.setattr(mobile, "require_db", lambda: db)
    app = FastAPI()
    app.include_router(mobile.router)
    return TestClient(app)


def test_pwa_shell_serves_installable_html(monkeypatch):
    c = _client(monkeypatch, _db([], []))
    r = c.get("/m")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ASVA" in r.text
    assert "/m/manifest.webmanifest" in r.text          # installable
    assert "serviceWorker" in r.text                    # registers the SW
    assert "View only" in r.text                         # the read-only banner


def test_pwa_manifest_is_valid(monkeypatch):
    c = _client(monkeypatch, _db([], []))
    r = c.get("/m/manifest.webmanifest")
    assert r.status_code == 200
    j = r.json()
    assert j["name"] == "ASVA" and j["display"] == "standalone" and j["start_url"] == "/m"


def test_pwa_service_worker_and_icon(monkeypatch):
    c = _client(monkeypatch, _db([], []))
    sw = c.get("/m/sw.js")
    assert sw.status_code == 200 and "caches" in sw.text
    ic = c.get("/m/icon.svg")
    assert ic.status_code == 200 and "svg" in ic.text.lower()


def test_api_end_to_end_auth_and_data(monkeypatch):
    monkeypatch.setattr(promises, "held_now", lambda db, bids: {})
    c = _client(monkeypatch, _db(_clients(), _bills()))
    assert c.get("/m/api/summary?token=nope").status_code == 401
    assert c.get("/m/api/summary").status_code == 401          # missing token
    r = c.get("/m/api/summary?token=tok-good")
    assert r.status_code == 200
    d = r.json()
    assert d["business_name"] == "RISHAB TRADING"
    assert d["parties_owing"] == 2 and d["total_outstanding"] == "8,000"
    assert [p["name"] for p in d["chase"]] == ["Ramesh", "Suresh"]


def test_qr_requires_valid_token(monkeypatch):
    c = _client(monkeypatch, _db([], []))
    assert c.get("/m/qr?token=nope").status_code == 401
    assert c.get("/m/qr").status_code == 401


def test_qr_returns_png_for_valid_token(monkeypatch):
    c = _client(monkeypatch, _db([], []))
    r = c.get("/m/qr?token=tok-good")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"      # real PNG bytes
