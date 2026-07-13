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
