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
    # Overdue repeats every 7 days until PAID or 200 days past due -
    # not just 3 times (the party keeps hearing until they pay).
    assert pts[37] == "overdue" and pts[44] == "overdue" and pts[58] == "overdue"
    assert pts[30 + 196] == "overdue"           # last repeat inside the window
    assert pts[30 + 7 * 29] == "escalate"       # then tell the owner (day 233)


def test_90_day_term_scales_up():
    # 3/7/15/21/30 of a 30-day term -> 9/21/45/63/90 of a 90-day term
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=90, due_offset=90))
    for d in (9, 21, 45, 63, 90):
        assert pts[d] == "nudge", (d, pts)
    # Overdue spacing also scales: ~21 days (7 * 90/30), not the old fixed 7.
    assert pts[111] == "overdue"        # 90 + 21
    assert pts[174] == "overdue"        # still repeating inside the window
    assert pts[90 + 21 * 10] == "escalate"   # window exhausted (day 300)
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
    assert pts[1 + 196] == "overdue"        # keeps going inside the window
    assert pts[1 + 7 * 29] == "escalate"    # day 204


def test_points_sorted_and_deduped():
    pts = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=45, due_offset=45)
    days = [d for d, _ in pts]
    assert days == sorted(days)
    assert len(days) == len(set(days))


# ── selection-day anchor: overdue track restarts from the day selected ─────

def test_anchor_restarts_overdue_track_from_selection_day():
    """User spec: party overdue 225 days, selected today -> messages every 7
    days from today until paid or day 425 (selection + 200), then escalate.
    Here modelled with a 200-day-old bill: track runs from day 200."""
    pts = kinds(cadence_points(DEFAULT_CADENCE, 7, 3,
                               credit_days=30, due_offset=30, overdue_from=200))
    assert pts[207] == "overdue" and pts[214] == "overdue" and pts[221] == "overdue"
    assert pts[200 + 196] == "overdue"        # keeps repeating inside window
    assert pts[200 + 7 * 29] == "escalate"    # ~selection + 200 days (403)
    assert 37 not in pts        # the old due-date-based overdue points are gone


def test_anchor_day_selection_sends_overdue_that_same_day():
    """On the selection day itself the party IS overdue, so the message that
    goes out that day is the OVERDUE message - factual, no pretending."""
    pts = cadence_points(DEFAULT_CADENCE, 7, 3,
                         credit_days=30, due_offset=30, overdue_from=200)
    day, kind = latest_reached_point(pts, 200)
    assert (day, kind) == (200, "overdue")
    # and 7 days later the next repeat
    day2, kind2 = latest_reached_point(pts, 207)
    assert (day2, kind2) == (207, "overdue")


def test_no_anchor_means_unchanged_behavior():
    """overdue_from omitted or equal to the due date = the original math."""
    a = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    b = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30,
                       overdue_from=30)
    assert a == b


def test_anchor_before_due_date_is_ignored():
    """Selecting a party whose bill is NOT yet overdue changes nothing -
    max(due, anchor) keeps the track on the due date."""
    a = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30)
    b = cadence_points(DEFAULT_CADENCE, 7, 3, credit_days=30, due_offset=30,
                       overdue_from=10)
    assert a == b


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
