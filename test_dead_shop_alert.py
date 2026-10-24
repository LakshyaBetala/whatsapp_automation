"""A shop that goes quiet has to reach a human.

Six of the first twelve shops stopped contacting the server within four days of
signing up, and three of those never contacted it at all. Nobody was told for
two months. Both holes were in `evaluate`:

  * a shop that has NEVER checked in was skipped outright, on the reasoning that
    setup is still in progress - true for an hour, false forever after;
  * a shop that HAS checked in and then went dark raised `shop_offline` at
    severity "warn", and alerts.reconcile only ever emails criticals.

So the product could lose a customer in a day and report nothing. These tests
pin the two cases that must now escalate, and the ordinary ones that must stay
quiet so the escalation keeps meaning something.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.config import settings
from app.services.monitoring import evaluate

DAY = 24 * 60


def _shop(**over) -> dict:
    row = {
        "id": "b1", "name": "Ramesh Traders", "status": "active",
        "online": True, "last_seen_min": 2, "never_seen": False, "age_min": 90 * DAY,
        "version": "2.2.8", "wa_ready": True, "wa_stale": False,
        "sent_today": 5, "failed_today": 0, "blocked_today": 0,
        "queued": 0, "queue_oldest_min": 0, "wa_transport": "server",
    }
    row.update(over)
    return row


def _problems(**over) -> dict:
    """kind -> problem, for one business."""
    health = {"system": {}, "jobs": [], "businesses": [_shop(**over)]}
    return {p["kind"]: p for p in evaluate(health)}


# ── never connected ───────────────────────────────────────────────────────
def test_a_fresh_signup_that_has_not_connected_yet_is_not_an_alarm():
    """Someone who signed up an hour ago is mid-setup, not lost."""
    got = _problems(never_seen=True, online=False, last_seen_min=10 ** 9, age_min=60)
    assert got == {}


def test_a_signup_that_never_connected_for_a_day_is_critical():
    got = _problems(never_seen=True, online=False, last_seen_min=10 ** 9, age_min=3 * DAY)
    p = got["shop_never_started"]
    assert p["severity"] == "critical", "warn is never emailed, so it would be silent again"
    assert p["business_id"] == "b1"
    assert "3 day" in p["body"]
    assert "never" in p["body"].lower()


def test_a_never_started_shop_does_not_also_raise_offline():
    """One shop, one story. Two alerts for the same fact trains you to ignore both."""
    got = _problems(never_seen=True, online=False, last_seen_min=10 ** 9, age_min=3 * DAY)
    assert "shop_offline" not in got and "shop_dying" not in got


def test_the_absurd_minutes_number_never_reaches_the_message():
    """last_seen_min for a never-seen shop is a sentinel, not a duration."""
    got = _problems(never_seen=True, online=False, last_seen_min=10 ** 9, age_min=2 * DAY)
    assert "10000" not in got["shop_never_started"]["body"]


# ── went quiet after working ──────────────────────────────────────────────
def test_a_shop_asleep_overnight_is_only_a_warning():
    """Shops close. A Sunday must not page anyone, or the real alert gets muted."""
    got = _problems(online=False, last_seen_min=10 * 60)
    assert got["shop_offline"]["severity"] == "warn"
    assert "shop_dying" not in got


def test_a_full_day_of_silence_is_critical():
    got = _problems(online=False, last_seen_min=26 * 60)
    p = got["shop_dying"]
    assert p["severity"] == "critical"
    assert "26 hours" in p["body"] or "26 hours" in p["title"]
    assert "shop_offline" not in got, "one shop, one story"


def test_the_threshold_is_configurable():
    before = settings.dead_shop_alert_h
    try:
        settings.dead_shop_alert_h = 48
        assert "shop_dying" not in _problems(online=False, last_seen_min=30 * 60)
        assert "shop_dying" in _problems(online=False, last_seen_min=50 * 60)
    finally:
        settings.dead_shop_alert_h = before


# ── things that must stay quiet ───────────────────────────────────────────
def test_a_suspended_shop_raises_nothing():
    """An expired trial is a decision, not an incident."""
    assert _problems(status="suspended", online=False, last_seen_min=90 * DAY) == {}


def test_a_healthy_shop_raises_nothing():
    assert _problems() == {}


def test_these_alerts_would_have_caught_the_real_losses():
    """The actual shapes from the database on 2026-10-25: three that never
    connected, three that stopped within a day or two. Every one must escalate."""
    rows = [
        _shop(id="surbhi", name="SURBHI", online=False, last_seen_min=81 * DAY),
        _shop(id="newgen", name="New Generation", never_seen=True, online=False,
              last_seen_min=10 ** 9, age_min=74 * DAY),
        _shop(id="supreme", name="SUPREME PETRO", never_seen=True, online=False,
              last_seen_min=10 ** 9, age_min=65 * DAY),
    ]
    problems = evaluate({"system": {}, "jobs": [], "businesses": rows})
    crit = [p for p in problems if p["severity"] == "critical"]
    assert {p["business_id"] for p in crit} == {"surbhi", "newgen", "supreme"}
