"""Unit tests for the reminder cadence engine (pure function).

The cadence scales with each party's credit period: [3,7,15,21,30]
= 10%/25%/50%/70%/100% of the term.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import (
    cadence_points,
    latest_reached_point,
    DEFAULT_CADENCE,
)


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
    # Overdue spacing also scales: ~21 days (7 * 90/30), not the old fixed 7.
    assert pts[111] == "overdue"        # 90 + 21
    assert pts[174] == "escalate"       # 90 + 21*4
    assert min(pts) == 9  # no day-3 nagging for a 90-day company


def test_overdue_spacing_scales_with_term():
    """A long-credit party gets wider overdue spacing, not weekly nagging."""
    p30 = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30))
    assert p30[37] == "overdue"         # 30-day term keeps ~7-day spacing
    p90 = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=90, due_offset=90))
    assert p90[111] == "overdue"        # 90-day term stretches to ~21-day
    assert 97 not in p90                # the old fixed-7 point is gone


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


# ── next-working-day / laptop-off catch-up (via latest_reached_point) ──────

def test_latest_point_none_before_first():
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    assert latest_reached_point(pts, 2) is None       # first nudge is day 3


def test_latest_point_returns_only_newest_reached():
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    # by day 16, points 3/7/15 are all "due" — only 15 comes back
    day, kind = latest_reached_point(pts, 16)
    assert (day, kind) == (15, "nudge")


def test_catch_up_never_stacks():
    """Laptop off across the day-15 point; on day 21 the sweep sends the day-21
    reminder only — not a backlog of 15 and 21."""
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    day, _ = latest_reached_point(pts, 21)
    assert day == 21
