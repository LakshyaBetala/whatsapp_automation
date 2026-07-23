"""License + heartbeat (migration 024): the server-authoritative subscription
answer the client must obey.

Covers licence-key shape, the status -> feature-flags mapping (paid actions
stop when suspended, read-only sync stays on), and build_heartbeat assembling
the right numbers for active / grace / suspended businesses.
"""
import datetime as _dt
import re
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import license as lic


def test_license_key_shape():
    k = lic.generate_license_key()
    assert re.fullmatch(r"ASVA-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", k), k
    # practically unique
    assert len({lic.generate_license_key() for _ in range(50)}) == 50


def test_feature_flags_by_status():
    for live in ("active", "grace"):
        f = lic.feature_flags(live)
        assert f == {"send": True, "reminders": True, "digest": True,
                     "ocr": True, "sync": True}
    s = lic.feature_flags("suspended")
    # paid actions stop; read-only Tally sync stays on
    assert s["send"] is False and s["reminders"] is False
    assert s["digest"] is False and s["ocr"] is False
    assert s["sync"] is True


def _patch_counts(monkeypatch, debtors, used, latest="1.3.0", mandatory=False, key="ASVA-AAAA-BBBB-CCCC"):
    monkeypatch.setattr(lic, "active_debtor_count", lambda db, bid: debtors)
    monkeypatch.setattr(lic, "messages_used_this_month", lambda db, bid, today=None: used)
    monkeypatch.setattr(lic, "_latest_release", lambda db: (latest, mandatory))
    monkeypatch.setattr(lic, "ensure_license_key", lambda db, biz: key)


def test_heartbeat_active_business(monkeypatch):
    _patch_counts(monkeypatch, debtors=180, used=270)
    today = _dt.date(2026, 7, 12)
    biz = {"id": "b1", "business_name": "RISHAB", "plan": "pro",
           "plan_expires_on": "2026-08-05"}
    hb = lic.build_heartbeat(db=None, biz=biz, today=today)

    assert hb["status"] == "active"
    assert hb["plan"] == "pro" and hb["plan_label"] == "Pro" and hb["price"] == 1999
    assert hb["days_left"] == 24
    assert hb["messages_used"] == 270 and hb["messages_limit"] == 8000
    assert hb["messages_remaining"] == 8000 - 270
    assert hb["active_debtors"] == 180 and hb["debtor_cap"] == 1000
    assert hb["over_debtor_cap"] is False
    assert hb["features"]["send"] is True
    assert hb["license_key"].startswith("ASVA-")


def test_heartbeat_suspended_blocks_paid_features(monkeypatch):
    _patch_counts(monkeypatch, debtors=1200, used=9000)
    today = _dt.date(2026, 7, 12)
    biz = {"id": "b1", "business_name": "X", "plan": "starter",
           "plan_expires_on": "2026-06-01"}  # long expired -> suspended
    hb = lic.build_heartbeat(db=None, biz=biz, today=today)

    assert hb["status"] == "suspended"
    assert hb["features"]["send"] is False
    assert hb["features"]["sync"] is True
    assert hb["over_debtor_cap"] is True             # 1200 > 300 starter cap
    assert hb["messages_remaining"] == 0             # 9000 used > 2400 limit


def test_heartbeat_grace_still_sends(monkeypatch):
    _patch_counts(monkeypatch, debtors=10, used=5)
    today = _dt.date(2026, 7, 12)
    # expired 2 days ago (< 5-day grace) -> grace, sends still allowed
    biz = {"id": "b1", "business_name": "X", "plan": "growth",
           "plan_expires_on": "2026-07-10"}
    hb = lic.build_heartbeat(db=None, biz=biz, today=today)
    assert hb["status"] == "grace"
    assert hb["days_left"] == -2
    assert hb["features"]["send"] is True


def test_heartbeat_update_available(monkeypatch):
    _patch_counts(monkeypatch, debtors=1, used=0, latest="9.9.9", mandatory=True)
    biz = {"id": "b1", "plan": "pro", "plan_expires_on": None}
    hb = lic.build_heartbeat(db=None, biz=biz, today=_dt.date(2026, 7, 12))
    assert hb["update_available"] is True
    assert hb["update_mandatory"] is True
    assert hb["status"] == "active"   # no expiry = internal/active


# ── Renewal cycle math ────────────────────────────────────────────────
def test_renew_on_time_stacks_from_expiry():
    today = _dt.date(2026, 7, 12)
    # active until Aug 5 -> renewing adds 30 days ONTO Aug 5 (no days lost)
    out = lic.renew_expiry("2026-08-05", months=1, today=today)
    assert out == _dt.date(2026, 8, 5) + _dt.timedelta(days=30)


def test_renew_late_starts_from_today():
    today = _dt.date(2026, 7, 12)
    # lapsed (expired Jun 1) -> renew from TODAY, not back-dated
    out = lic.renew_expiry("2026-06-01", months=1, today=today)
    assert out == today + _dt.timedelta(days=30)


def test_renew_no_prior_expiry_from_today():
    today = _dt.date(2026, 7, 12)
    out = lic.renew_expiry(None, months=2, today=today)
    assert out == today + _dt.timedelta(days=60)


# ── Onboarding (create_business) ──────────────────────────────────────
class _InsertQ:
    def __init__(self, sink):
        self._sink = sink
        self._row = None

    def insert(self, row):
        self._row = dict(row)
        return self

    def execute(self):
        self._sink.append(self._row)
        return type("R", (), {"data": [self._row]})()


class _InsertDB:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        return _InsertQ(self.inserted)


def test_normalize_wa_number():
    assert lic.normalize_wa_number("9876543210") == "919876543210"
    assert lic.normalize_wa_number("09876543210") == "919876543210"
    assert lic.normalize_wa_number("+91 98765 43210") == "919876543210"
    assert lic.normalize_wa_number("919876543210") == "919876543210"


def test_create_business_mints_token_key_expiry():
    db = _InsertDB()
    today = _dt.date(2026, 7, 13)
    biz = lic.create_business(db, owner_name="Papa", business_name="Rishab Trading",
                              whatsapp_number="98765 43210", plan="pro", months=1, today=today)
    assert biz["whatsapp_number"] == "919876543210"
    assert biz["plan"] == "pro"
    assert biz["business_name"] == "Rishab Trading"
    assert biz["agent_token"] and len(biz["agent_token"]) >= 20
    assert biz["license_key"].startswith("ASVA-")
    assert biz["plan_expires_on"] == (today + _dt.timedelta(days=30)).isoformat()
    assert biz["onboarding_status"] == "active"
    assert db.inserted and db.inserted[0]["agent_token"] == biz["agent_token"]


def test_create_business_rejects_bad_input():
    db = _InsertDB()
    for bad in (lambda: lic.create_business(db, owner_name="x", whatsapp_number="123"),
                lambda: lic.create_business(db, owner_name="x", whatsapp_number="9876543210", plan="ultra"),
                lambda: lic.create_business(db, owner_name="x", whatsapp_number="9876543210", months=0)):
        try:
            bad()
            assert False, "should have raised"
        except ValueError:
            pass
    assert db.inserted == []          # nothing written on bad input


def test_create_business_endpoint_admin_gate(monkeypatch):
    import asyncio
    from app.routers import license as lr
    from fastapi import BackgroundTasks, HTTPException
    monkeypatch.setattr(lr.settings, "admin_api_key", "s3cret")
    try:
        asyncio.run(lr.create_business(lr.CreateBizPayload(
            admin_key="wrong", owner_name="x", whatsapp_number="9876543210"),
            BackgroundTasks()))
        assert False, "should have refused"
    except HTTPException as e:
        assert e.status_code == 401


def test_renew_endpoint_admin_gate(monkeypatch):
    """/license/renew refuses without the configured admin key."""
    import asyncio
    from app.routers import license as lr
    from fastapi import HTTPException

    # key unset -> 503 (feature disabled)
    monkeypatch.setattr(lr.settings, "admin_api_key", "")
    try:
        asyncio.run(lr.renew(lr.RenewPayload(admin_key="x", agent_token="t")))
        assert False, "should have refused"
    except HTTPException as e:
        assert e.status_code == 503

    # key set but wrong -> 401
    monkeypatch.setattr(lr.settings, "admin_api_key", "s3cret")
    try:
        asyncio.run(lr.renew(lr.RenewPayload(admin_key="wrong", agent_token="t")))
        assert False, "should have refused"
    except HTTPException as e:
        assert e.status_code == 401
