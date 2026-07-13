"""Unit tests for the subscription lifecycle (server-side license)."""
from datetime import date, timedelta

from app.services.subscription import effective_status, days_left, GRACE_DAYS

TODAY = date(2026, 7, 5)


def test_active_before_expiry():
    assert effective_status(TODAY + timedelta(days=10), TODAY) == "active"
    assert effective_status(TODAY, TODAY) == "active"  # expiry day itself


def test_grace_window_is_three_days():
    assert GRACE_DAYS == 3
    assert effective_status(TODAY - timedelta(days=1), TODAY) == "grace"
    assert effective_status(TODAY - timedelta(days=2), TODAY) == "grace"


def test_suspended_after_grace():
    assert effective_status(TODAY - timedelta(days=3), TODAY) == "suspended"
    assert effective_status(TODAY - timedelta(days=90), TODAY) == "suspended"


def test_no_expiry_means_active():
    assert effective_status(None, TODAY) == "active"


def test_string_dates_accepted():
    assert effective_status("2026-07-10", TODAY) == "active"
    assert effective_status("2026-06-25", TODAY) == "suspended"


def test_days_left():
    assert days_left(TODAY + timedelta(days=5), TODAY) == 5
    assert days_left(TODAY - timedelta(days=2), TODAY) == -2
    assert days_left(None, TODAY) is None
