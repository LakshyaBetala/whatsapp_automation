"""Unit tests for the reminder cadence engine (pure function).

The cadence scales with each party's credit period: [3,7,15,21,30]
= 10%/25%/50%/70%/100% of the term.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import cadence_points, DEFAULT_CADENCE


def kinds(points):
    return {day: kind for day, kind in points}


def test_30_day_term_is_the_authored_cadence():
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30))
    for d in (3, 7, 15, 21, 30):
        assert pts[d] == "nudge"
    assert pts[37] == "overdue" and pts[44] == "overdue" and pts[51] == "overdue"
    assert pts[58] == "escalate"


def test_90_day_term_scales_up():
    # 3/7/15/21/30 of a 30-day term -> 9/21/45/63/90 of a 90-day term
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=90, due_offset=90))
    for d in (9, 21, 45, 63, 90):
        assert pts[d] == "nudge", (d, pts)
    assert pts[97] == "overdue"
    assert pts[118] == "escalate"
    assert min(pts) == 9  # no day-3 nagging for a 90-day company


def test_short_term_compresses():
    # 7-day term: points compress into the week, no zero-day sends
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=7, due_offset=7)
    days = [d for d, _ in pts]
    assert min(days) >= 1
    assert kinds(pts)[7] == "nudge"        # due day touch exists
    assert kinds(pts)[14] == "overdue"     # then the overdue track


def test_one_day_term_goes_straight_to_overdue_track():
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=1, due_offset=1))
    assert pts[1] == "nudge"
    assert pts[8] == "overdue" and pts[15] == "overdue" and pts[22] == "overdue"
    assert pts[29] == "escalate"


def test_points_sorted_and_deduped():
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=45, due_offset=45)
    days = [d for d, _ in pts]
    assert days == sorted(days)
    assert len(days) == len(set(days))
