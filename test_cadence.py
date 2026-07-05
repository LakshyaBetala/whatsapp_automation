"""Unit tests for the reminder cadence engine (pure function)."""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import cadence_points, DEFAULT_CADENCE


def kinds(points):
    return {day: kind for day, kind in points}


def test_regular_trade_full_cadence():
    # 30-day default terms: nudges at 3/7/15/21/30, then overdue at 37/44/51, escalate 58
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30))
    assert pts[3] == "nudge" and pts[7] == "nudge" and pts[21] == "nudge"
    assert pts[30] == "nudge"          # due day itself is still a nudge
    assert pts[37] == "overdue" and pts[44] == "overdue" and pts[51] == "overdue"
    assert pts[58] == "escalate"


def test_immediate_due_bill_goes_overdue_fast():
    # 1-day terms (common in Chennai trade): day-3 nudge point is already past due
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=1, due_offset=1))
    assert pts[3] == "overdue"
    assert pts[7] == "overdue"


def test_long_credit_terms_no_early_nagging():
    # 90-day company: no nudges at all before due, one courtesy at due-3
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=90, due_offset=90)
    days = [d for d, _ in pts]
    assert min(days) == 87                       # nothing before due-3
    assert kinds(pts)[87] == "predue"
    assert kinds(pts)[97] == "overdue"
    assert kinds(pts)[118] == "escalate"


def test_collision_strongest_kind_wins():
    # cadence point falls exactly on an overdue repeat day
    pts = kinds(cadence_points([7, 14], 7, 1, credit_days=30, due_offset=7))
    assert pts[14] == "overdue"   # nudge(14) collides with due+7 overdue → overdue wins
    assert pts[21] == "escalate"


def test_points_sorted_ascending():
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    days = [d for d, _ in pts]
    assert days == sorted(days)
