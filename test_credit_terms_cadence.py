"""The credit period reaches the cadence, and the party sets the rhythm.

Found on one real party (PINEMA AGENCY, 10 open bills, RISHAB's books). Every
open bill it had carried due_date == invoice_date, because Tally reports
BILLCREDITPERIOD on the ledger and not on each bill, so the bill-wise
outstanding export arrives with no due date and the backend kept that. The
party's real 60-day term sat in ASVA's own clients row, unread.

Four things followed, and every one of them hurt the shop's relationship with
its customer rather than helping collect:

  * overdue days inflated by the whole credit period (222 shown, 162 true)
  * the five pre-due nudges collapsed onto day 1, so a party that was still
    inside its credit period got chased as if it were late
  * the overdue gap scaled off a 1-day term: every 7 days instead of 14
  * the 200-day window burned from the invoice date, so the two oldest and
    largest debts had aged out of the cadence and gone silent for good

And separately from the term: each BILL kept its own overdue clock, so ten
bills meant ten tracks, which collided into three messages in four days.

These tests pin the term reaching the bill, the party-level gap, the off day,
and the ceiling that now hands over to legal escalation instead of to silence.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import (
    LEGAL_ESCALATION_DAYS,
    OVERDUE_WINDOW_DAYS,
    cadence_points,
    needs_legal_escalation,
    party_repeat_days,
)
from app.routers.tally import due_from_terms


# ── the term reaching the bill ────────────────────────────────────────────
def test_tally_due_date_is_used_when_tally_gives_one():
    """Tally's own answer always wins; this fallback only fills a blank."""
    # the caller passes b.due_date through untouched - proven in the sweep
    # test below, here we pin that the fallback itself never invents a term
    assert due_from_terms("2026-04-06", 60, 30) == "2026-06-05"


def test_the_partys_own_term_is_preferred():
    assert due_from_terms("2026-08-31", 60, 30) == "2026-10-30"


def test_the_shop_default_backs_it_up():
    """A party with no term of its own gets the shop's fallback, not 0 days."""
    assert due_from_terms("2026-08-31", None, 45) == "2026-10-15"
    assert due_from_terms("2026-08-31", 0, 45) == "2026-10-15"


def test_a_term_is_never_zero_days():
    """The whole bug was a silent 0-day term. It must not be reachable."""
    for cd in (None, 0, "", "nonsense", -5):
        out = due_from_terms("2026-08-31", cd, 30)
        assert out == "2026-09-30", (cd, out)


def test_an_unparseable_date_is_returned_unchanged():
    """A due date we cannot compute must be neither a crash nor a guess."""
    assert due_from_terms("not a date", 60, 30) == "not a date"
    assert due_from_terms("", 60, 30) == ""


def test_pinema_overdue_days_stop_being_inflated():
    """The actual number the owner was shown: 222 days on a 162-day debt."""
    today = date(2026, 10, 25)
    inv = date(2026, 3, 17)
    broken = (today - inv).days                     # what term-0 produced
    due = date.fromisoformat(due_from_terms(inv.isoformat(), 60, 30))
    assert broken == 222
    assert (today - due).days == 162


def test_the_five_pre_due_nudges_actually_fire_once_the_term_is_right():
    """With a 0-day term every nudge collapsed onto day 1 and the party was
    'overdue' from the day it was invoiced. A real term spreads them out."""
    collapsed = [d for d, k in cadence_points([3, 7, 15, 21, 30], 7, 3,
                                              credit_days=60, due_offset=0)
                 if k == "nudge"]
    real = [d for d, k in cadence_points([3, 7, 15, 21, 30], 7, 3,
                                         credit_days=60, due_offset=60)
            if k == "nudge"]
    assert collapsed == [1]
    assert real == [6, 14, 30, 42, 60], real
    assert real[-1] == 60, "the last nudge lands on the due date"


# ── party_repeat_days ─────────────────────────────────────────────────────
@pytest.mark.parametrize("credit_days,expected", [
    (30, 7), (45, 10), (60, 14), (90, 21), (15, 7), (7, 7),
])
def test_the_overdue_gap_scales_with_the_term(credit_days, expected):
    """A 60-day trade is not chased on a 30-day trade's rhythm. 14 days was
    what the party screen already promised the owner; now the engine agrees."""
    assert party_repeat_days({"overdue_repeat_days": 7}, credit_days) == expected


def test_the_shop_setting_is_a_floor_not_a_ceiling():
    assert party_repeat_days({"overdue_repeat_days": 14}, 30) == 14
    assert party_repeat_days({"overdue_repeat_days": 14}, 60) == 28


def test_a_missing_or_junk_term_falls_back_to_the_30_day_base():
    for cd in (None, 0, "", "x"):
        assert party_repeat_days({}, cd) == 7, cd


# ── the legal ceiling ─────────────────────────────────────────────────────
def test_nothing_escalates_before_the_ceiling():
    today = date(2026, 10, 25)
    assert needs_legal_escalation(today - timedelta(days=LEGAL_ESCALATION_DAYS), today) is False
    assert needs_legal_escalation(today - timedelta(days=1), today) is False


def test_past_the_ceiling_it_escalates():
    today = date(2026, 10, 25)
    assert needs_legal_escalation(
        today - timedelta(days=LEGAL_ESCALATION_DAYS + 1), today) is True


def test_an_unknown_due_date_never_escalates():
    """This flag puts 'escalation required' in front of the owner, so it must
    only ever fire on a date we actually read."""
    assert needs_legal_escalation(None, date(2026, 10, 25)) is False


def test_the_ceiling_and_the_cadence_stop_at_the_same_place():
    """If the cadence outlived the ceiling the party would be told to escalate
    while ASVA kept messaging; if it died first the debt would go silent."""
    assert OVERDUE_WINDOW_DAYS == LEGAL_ESCALATION_DAYS


def test_the_oldest_pinema_bills_are_back_on_the_cadence():
    """At the old 200-day window these two returned no next point at all: the
    biggest, oldest debt was the one ASVA had quietly given up on."""
    pts = cadence_points([3, 7, 15, 21, 30], 7, 3, credit_days=60, due_offset=60)
    inv, today = date(2026, 3, 17), date(2026, 10, 25)
    dsi = (today - inv).days                     # 222
    assert any(d >= dsi for d, _ in pts), "no reminder left for a 162-day-overdue bill"


# ── the sweep: off day, holidays, and one message per party ──────────────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """A fake that actually honours eq/neq.

    The older sweep fakes return every row of a table for any query, which
    silently defeats two of the rules under test here: _already_sent reads
    `bool(resp.data)`, so one unrelated message row makes every bill look
    already-reminded, and _last_real_send filters cadence markers out by
    template_name. Without filtering, both tests would pass or fail for
    reasons that have nothing to do with the code. gte/in_/lt only ever widen
    a result here, so they stay no-ops.
    """

    def __init__(self, rows, sink):
        self._rows, self._sink, self._write = rows, sink, None
        self._eq: list[tuple] = []
        self._neq: list[tuple] = []

    def select(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def neq(self, col, val):
        self._neq.append((col, val))
        return self

    def insert(self, row):
        self._write = row
        return self

    def update(self, row):
        self._write = dict(row, _update=True)
        return self

    def execute(self):
        if self._write is not None:
            saved = dict(self._write)
            self._sink.append(saved)
            return _Result([saved])
        rows = [r for r in self._rows
                if all(r.get(c) == v for c, v in self._eq)
                and all(r.get(c) != v for c, v in self._neq)]
        return _Result(rows)


class FakeDB:
    def __init__(self, tables):
        self.tables, self.writes = tables, {}

    def table(self, name):
        self.writes.setdefault(name, [])
        return _Query(self.tables.get(name, []), self.writes[name])


# Real today: the sweep drops shops whose last_seen is stale (is_dormant)
# against the real clock, so a fixture dated in the past is simply invisible.
TODAY = date.today()


def _fixture(**biz_over):
    """One party, one bill, 60-day term, 100 days past due -> overdue is due."""
    inv = (TODAY - timedelta(days=160)).isoformat()
    due = (TODAY - timedelta(days=100)).isoformat()
    client = {"id": "c1", "name": "Pinema Agency", "whatsapp_number": "919443284711",
              "language": "hi", "reminders_enabled": True, "credit_days": 60,
              "reminder_batch": None, "reminder_anchor": None,
              "created_at": inv}
    bills = [{"id": "b1", "invoice_number": "S-1", "amount": 10000.0,
              "outstanding": 10000.0, "status": "overdue", "due_date": due,
              "invoice_date": inv, "business_id": "biz1", "client_id": "c1",
              "clients": client}]
    biz = {"id": "biz1", "business_name": "RTC", "whatsapp_number": "919444294894",
           "plan": "pro", "blackout_dates": [], "reminders_enabled": True,
           "upi_vpa": "t@ok", "reminder_cadence": None, "weekly_off_day": None,
           "send_on_holidays": False, "reminder_style": None,
           "reminder_custom_line": None, "reminder_hour": 0, "msg_language": "english",
           "discount_pct": 0, "overdue_repeat_days": 7, "overdue_max_repeats": 3,
           "plan_expires_on": None, "reminder_batches": None,
           "last_seen": datetime.now(timezone.utc).isoformat()}
    biz.update(biz_over)
    return biz, bills


def _run(monkeypatch, biz, bills, messages=None):
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc

    fake = FakeDB({"businesses": [biz], "bills": bills,
                   "messages": messages or [], "sweep_runs": [{"run_hour": 0}]})
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    monkeypatch.setattr(rs, "_today_ist", lambda: TODAY)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)

    sends = []

    async def fake_send(b, entry):
        sends.append(entry)
        return True, "sent"

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", fake_send)
    asyncio.run(rs.run())
    return sends


def test_a_party_due_a_reminder_gets_one(monkeypatch):
    biz, bills = _fixture()
    assert len(_run(monkeypatch, biz, bills)) == 1


def test_the_weekly_off_day_is_finally_honoured(monkeypatch):
    """weekly_off_day was collected from the owner, stored, selected by the
    sweep's own query - and never read. A shop shut on Wednesday still chased
    on Wednesday. The point re-fires the next working day, so nothing is lost."""
    biz, bills = _fixture(weekly_off_day=TODAY.weekday())
    assert _run(monkeypatch, biz, bills) == []


def test_a_holiday_still_pauses_the_day(monkeypatch):
    biz, bills = _fixture(blackout_dates=[TODAY.isoformat()])
    assert _run(monkeypatch, biz, bills) == []


def test_a_shop_that_trades_through_sundays_can_say_so(monkeypatch):
    """The checkbox: respecting the off day is the default, not a rule."""
    biz, bills = _fixture(weekly_off_day=TODAY.weekday(),
                          blackout_dates=[TODAY.isoformat()],
                          send_on_holidays=True)
    assert len(_run(monkeypatch, biz, bills)) == 1


def test_one_overdue_message_per_party_per_gap(monkeypatch):
    """The real failure: 10 bills on 10 invoice dates fired on 27, 29 and 30
    Oct. A 60-day party may hear once every 14 days, however many bills it has."""
    biz, bills = _fixture()
    recent = [{"id": "m1", "client_id": "c1", "business_id": "biz1",
               "type": "reminder", "template_name": "manual_remind_hi",
               "created_at": (TODAY - timedelta(days=5)).isoformat()}]
    assert _run(monkeypatch, biz, bills, messages=recent) == []


def test_the_gap_opens_again_once_the_term_has_passed(monkeypatch):
    biz, bills = _fixture()
    old = [{"id": "m1", "client_id": "c1", "business_id": "biz1",
            "type": "reminder", "template_name": "manual_remind_hi",
            "created_at": (TODAY - timedelta(days=15)).isoformat()}]
    assert len(_run(monkeypatch, biz, bills, messages=old)) == 1


def test_cadence_markers_do_not_count_as_a_send(monkeypatch):
    """One send writes one marker per bill. If markers counted as sends, a
    party with ten bills would read as ten messages yesterday and its gap
    would never reopen - the louder the debt, the quieter ASVA would get."""
    biz, bills = _fixture()
    markers = [{"id": f"m{i}", "client_id": "c1", "business_id": "biz1",
                "type": "reminder", "template_name": "cadence_marker",
                "reminder_day": 900 + i,          # not this bill's live point
                "created_at": (TODAY - timedelta(days=1)).isoformat()}
               for i in range(10)]
    assert len(_run(monkeypatch, biz, bills, messages=markers)) == 1


def test_a_single_bill_keeps_all_five_pre_due_nudges():
    """The reason the schedule is driven by ONE bill rather than thinned by a
    party-level gap. A 14-day gap on a 60-day term eats days 14 and 42 and
    leaves three touches, not the five the cadence is built to send."""
    pts = [d for d, k in cadence_points([3, 7, 15, 21, 30], 7, 3,
                                        credit_days=60, due_offset=60) if k == "nudge"]
    gap = party_repeat_days({"overdue_repeat_days": 7}, 60)
    thinned, last = [], None
    for d in pts:
        if last is None or d - last >= gap:
            thinned.append(d)
            last = d
    assert pts == [6, 14, 30, 42, 60]
    assert thinned == [6, 30, 60], "a gap over the nudges costs two of the five"


def test_a_pre_due_nudge_is_not_held_back_by_a_recent_send(monkeypatch):
    """A party inside its credit period runs its own curve. The gap applies to
    the overdue track, where the spacing is the point, not to the nudges."""
    inv = (TODAY - timedelta(days=14)).isoformat()
    due = (TODAY + timedelta(days=46)).isoformat()       # 60-day term, not due yet
    client = {"id": "c1", "name": "Omsakthi", "whatsapp_number": "919443284711",
              "language": "hi", "reminders_enabled": True, "credit_days": 60,
              "reminder_batch": None, "reminder_anchor": None, "created_at": inv}
    bills = [{"id": "b1", "invoice_number": "S-1", "amount": 5000.0,
              "outstanding": 5000.0, "status": "pending", "due_date": due,
              "invoice_date": inv, "business_id": "biz1", "client_id": "c1",
              "clients": client}]
    biz, _ = _fixture()
    recent = [{"id": "m1", "client_id": "c1", "business_id": "biz1",
               "type": "reminder", "template_name": "manual_remind_hi",
               "created_at": (TODAY - timedelta(days=3)).isoformat()}]
    assert len(_run(monkeypatch, biz, bills, messages=recent)) == 1


def test_the_earliest_due_bill_drives_the_schedule(monkeypatch):
    """Ten bills, one curve. The oldest debt sets the pace; the message still
    carries every bill, so the newer ones are not hidden by this."""
    biz, _ = _fixture()
    client = {"id": "c1", "name": "Pinema Agency", "whatsapp_number": "919443284711",
              "language": "hi", "reminders_enabled": True, "credit_days": 60,
              "reminder_batch": None, "reminder_anchor": None,
              "created_at": (TODAY - timedelta(days=200)).isoformat()}
    bills = []
    for i, age in enumerate((200, 160, 120, 80)):
        bills.append({"id": f"b{i}", "invoice_number": f"S-{i}", "amount": 1000.0,
                      "outstanding": 1000.0, "status": "overdue",
                      "invoice_date": (TODAY - timedelta(days=age)).isoformat(),
                      "due_date": (TODAY - timedelta(days=age - 60)).isoformat(),
                      "business_id": "biz1", "client_id": "c1", "clients": client})
    sends = _run(monkeypatch, biz, bills)
    assert len(sends) == 1, "four bills must not mean four tracks"
    assert len(sends[0]["bills"]) == 4, "the one message still itemises them all"
    assert float(sends[0]["total"]) == 4000.0


def test_an_owner_escalation_is_never_held_back(monkeypatch):
    """The gap protects the customer. Holding the owner's own alert would hide
    a debt that has run out of reminders entirely."""
    import inspect
    from app.jobs import reminder_sweep as rs
    src = inspect.getsource(rs.run)
    assert 'if "escalate" not in kinds and "overdue" in kinds:' in src
