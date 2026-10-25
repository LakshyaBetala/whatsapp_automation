"""Past the 300-day cadence, what decides the next step is the LIMITATION
runway, not the days overdue. These tests pin the four states, the rupee floor,
the Udyam fork in the procedure, and the one thing that must never regress: the
notice draft never claims to have been sent.
"""
import datetime as dt
import sys
from decimal import Decimal
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import OVERDUE_WINDOW_DAYS
from app.services import legal

TODAY = dt.date(2026, 10, 26)


def _bill(days_past_due: int, amount, **kw) -> dict:
    due = TODAY - dt.timedelta(days=days_past_due)
    return {"due_date": due.isoformat(), "outstanding": amount,
            "invoice_date": (due - dt.timedelta(days=30)).isoformat(),
            "invoice_number": kw.get("inv", "S-1")}


# ── The four states ────────────────────────────────────────────────────────

def test_a_bill_still_inside_the_cadence_has_no_legal_state():
    """ASVA is still chasing it on WhatsApp, so the legal card must stay away."""
    assert legal.party_legal([_bill(OVERDUE_WINDOW_DAYS - 1, 50000)], TODAY) is None


def test_just_past_the_cadence_is_worth_a_notice():
    st = legal.party_legal([_bill(OVERDUE_WINDOW_DAYS + 1, 50000)], TODAY)
    assert st["state"] == legal.NOTICE
    assert st["amount"] == Decimal("50000")
    assert st["lost"] == 0


def test_under_six_months_of_runway_is_act_now_and_names_the_date():
    # 3 years minus 100 days past due -> 100 days of runway left.
    past = legal.LIMITATION_DAYS - 100
    st = legal.party_legal([_bill(past, 50000)], TODAY)
    assert st["state"] == legal.ACT_NOW
    assert st["deadline"] == TODAY + dt.timedelta(days=100)
    # The label must carry the date - "escalation required" is what we replaced.
    assert st["deadline"].strftime("%d %b %Y") in st["label"]
    assert "Act before" in st["label"]


def test_past_three_years_is_time_barred_and_counted_as_lost():
    st = legal.party_legal([_bill(legal.LIMITATION_DAYS + 1, 9000)], TODAY)
    assert st["state"] == legal.TIME_BARRED
    assert st["lost"] == Decimal("9000") and st["amount"] == 0


def test_the_boundary_day_is_still_enforceable():
    """Exactly three years = the last day there is still a claim. One day's
    arithmetic error here is a lost claim, not a rounding error."""
    assert legal.party_legal([_bill(legal.LIMITATION_DAYS, 9000)],
                             TODAY)["state"] != legal.TIME_BARRED
    assert legal.party_legal([_bill(legal.LIMITATION_DAYS + 1, 9000)],
                             TODAY)["state"] == legal.TIME_BARRED


# ── The rupee floor ───────────────────────────────────────────────────────

def test_a_tiny_debt_is_not_worth_a_notice():
    st = legal.party_legal([_bill(400, 1200)], TODAY)
    assert st["state"] == legal.SMALL


def test_small_bills_add_up_to_one_claim_worth_chasing():
    """The floor applies to the PARTY's total, not to each bill: five bills of
    Rs 1,500 are one Rs 7,500 claim, and a per-bill floor would hide it."""
    bills = [_bill(400, 1500, inv=f"S-{i}") for i in range(5)]
    assert legal.party_legal(bills, TODAY)["state"] == legal.NOTICE


def test_a_small_debt_that_is_expiring_still_shows_the_date():
    """Too small for a notice, but a Rs 500 part payment buys three more years -
    so the deadline has to stay visible."""
    past = legal.LIMITATION_DAYS - 30
    st = legal.party_legal([_bill(past, 1500)], TODAY)
    assert st["state"] == legal.SMALL
    assert st["deadline"] is not None
    assert st["deadline"].strftime("%d %b %Y") in st["label"]


# ── Mixed parties: the actionable half wins the label ─────────────────────

def test_live_and_dead_money_on_one_party_reports_both():
    bills = [_bill(legal.LIMITATION_DAYS + 50, 20000, inv="OLD"),
             _bill(400, 30000, inv="NEW")]
    st = legal.party_legal(bills, TODAY)
    assert st["state"] == legal.NOTICE          # act on what can still be acted on
    assert st["amount"] == Decimal("30000")
    assert st["lost"] == Decimal("20000")       # and say what is gone


def test_an_expiring_bill_outranks_a_comfortable_one():
    bills = [_bill(400, 30000, inv="OK"),
             _bill(legal.LIMITATION_DAYS - 20, 10000, inv="URGENT")]
    st = legal.party_legal(bills, TODAY)
    assert st["state"] == legal.ACT_NOW
    assert st["deadline"] == TODAY + dt.timedelta(days=20)


def test_a_bill_with_no_readable_due_date_is_ignored():
    """A guessed date must never drive a legal decision."""
    assert legal.party_legal([{"due_date": None, "outstanding": 90000}], TODAY) is None
    assert legal.party_legal([{"due_date": "not a date", "outstanding": 90000}],
                             TODAY) is None


def test_a_settled_bill_does_not_count():
    assert legal.party_legal([_bill(400, 0)], TODAY) is None


def test_runway_days_is_none_when_the_date_cannot_be_read():
    assert legal.runway_days(None) is None
    assert legal.runway_days("2023-01-01", TODAY) == legal.LIMITATION_DAYS - (
        TODAY - dt.date(2023, 1, 1)).days


# ── The Udyam fork ────────────────────────────────────────────────────────

def test_without_udyam_the_steps_tell_the_owner_to_register():
    steps = legal.procedure(None)
    text = " ".join(h + " " + t for h, t in steps)
    assert "udyamregistration.gov.in" in text
    assert "Order XXXVII" in text          # the civil route
    assert "odr.msme.gov.in" not in text   # not available to them yet


def test_with_udyam_the_steps_route_to_the_msme_portal():
    steps = legal.procedure("UDYAM-TN-00-1234567")
    text = " ".join(h + " " + t for h, t in steps)
    assert "odr.msme.gov.in" in text
    assert "Samadhaan no longer takes new filings" in text
    assert "75%" in text                   # the deposit that creates the pressure


def test_part_payment_comes_before_any_notice_in_the_steps():
    """Collecting Rs 500 restarts three years. It is cheaper than a notice and
    has to be offered first, whichever route the shop is on."""
    for udyam in (None, "UDYAM-TN-00-1234567"):
        heads = [h for h, _ in legal.procedure(udyam)]
        part = next(i for i, h in enumerate(heads) if "part payment" in h.lower())
        notice = next(i for i, h in enumerate(heads) if "notice" in h.lower())
        assert part < notice


# ── The notice draft ──────────────────────────────────────────────────────

def _notice(udyam=None, address="12 Main Road\nChennai 600001"):
    bills = [_bill(400, 30000, inv="S-101"), _bill(380, 12500, inv="S-102")]
    return legal.notice_text(
        shop="RISHAB TRADING COMPANY", shop_address=address, party="Pinema Agency",
        bills=bills, total=Decimal("42500"), udyam_number=udyam,
        today=TODAY, oldest_days=400)


def test_the_notice_is_marked_a_draft_and_never_claims_to_be_sent():
    t = _notice()
    assert t.splitlines()[0].startswith("DRAFT")
    assert "SIGN AND SERVE" in t.upper()
    assert "registered post AD" in t


def test_the_notice_carries_the_ledger_and_the_total():
    t = _notice()
    assert "S-101" in t and "S-102" in t
    assert "42,500" in t                      # Indian grouping, not 42,500.00
    assert "Pinema Agency" in t and "RISHAB TRADING COMPANY" in t
    assert "FIFTEEN (15) DAYS" in t


def test_a_missing_address_becomes_a_visible_placeholder():
    assert "[your shop address]" in _notice(address=None)


def test_only_a_udyam_shop_claims_msmed_interest():
    plain, msme = _notice(), _notice("UDYAM-TN-00-1234567")
    assert "MSMED" not in plain and "section 16" not in plain
    assert "Order XXXVII" in plain
    assert "section 16" in msme and "compounded monthly" in msme
    assert "UDYAM-TN-00-1234567" in msme
    # The rate it used must be on the page, because the rate moves.
    assert str(legal.RBI_BANK_RATE_PCT) in msme


def test_the_interest_estimate_grows_with_time_and_is_never_negative():
    a = legal.msmed_interest(100000, 90)
    b = legal.msmed_interest(100000, 900)
    assert 0 < a < b
    assert legal.msmed_interest(100000, 0) == 0
    assert legal.msmed_interest(0, 900) == 0
    assert legal.msmed_interest(-5000, 900) == 0


def test_indian_digit_grouping():
    assert legal._inr(2337282) == "23,37,282"
    assert legal._inr(1640) == "1,640"
    assert legal._inr(100) == "100"


# ── The ceiling stays one number ──────────────────────────────────────────

def test_the_legal_card_starts_exactly_where_the_cadence_stops():
    """If someone moves OVERDUE_WINDOW_DAYS, the handover has to move with it,
    or a party falls in the gap between the last reminder and the legal card."""
    assert legal.party_legal([_bill(OVERDUE_WINDOW_DAYS, 90000)], TODAY) is None
    assert legal.party_legal([_bill(OVERDUE_WINDOW_DAYS + 1, 90000)], TODAY) is not None


# ── The screens ───────────────────────────────────────────────────────────
# These render the real pages, because the whole point of the change is what
# the owner reads. The unit tests above cannot catch a card that never got
# interpolated into the HTML.

import pytest                                              # noqa: E402
from conftest import maybe_await                            # noqa: E402
from app.routers import admin                               # noqa: E402

BIZ = {
    "id": "b1", "business_name": "Test Traders", "agent_token": "tok",
    "weekly_off_day": None, "blackout_dates": [], "reminder_style": "standard",
    "reminder_custom_line": "", "reminder_hour": 10, "msg_language": "english",
    "owner_language": "english", "discount_pct": 0, "plan": "starter",
    "upi_vpa": "shop@upi", "upi_vpa_2": "", "upi_vpa_3": "",
    "whatsapp_number": "919000000000", "reminder_cadence": None,
    "bank_account_name": "", "bank_account_no": "", "bank_ifsc": "", "bank_name": "",
    "overdue_repeat_days": 7, "overdue_max_repeats": 20, "plan_expires_on": None,
    "catchup_date": None, "catchup_action": None, "wa_ready": True,
    "wa_checked_at": None, "share_data": True, "tally_deposit_ledgers": ["Cash"],
    # Two batches, so the Batch column renders its chips.
    "reminder_batches": [{"name": "Morning", "lang": "english", "hour": 11},
                         {"name": "Evening", "lang": "hinglish", "hour": 18}],
}
CLIENT = {
    "id": "c1", "name": "Old Debtor", "whatsapp_number": "919111111111",
    "credit_days": 30, "reminders_enabled": True, "excluded": False,
    "tally_ledger_name": "Old Debtor", "language": "english", "reminder_batch": 0,
    "reminder_anchor": None, "created_at": "2022-01-01T00:00:00+00:00",
}


def _fake(bill_days_past_due: int, amount, udyam=None):
    due = TODAY - dt.timedelta(days=bill_days_past_due)
    bill = {"id": "bl1", "invoice_number": "S-900", "amount": amount,
            "paid_amount": 0, "outstanding": amount, "status": "overdue",
            "due_date": due.isoformat(),
            "invoice_date": (due - dt.timedelta(days=30)).isoformat(),
            "is_opening_balance": False, "source": "tally",
            "client_id": "c1", "business_id": "b1", "clients": dict(CLIENT)}

    class _R:
        def __init__(self, d):
            self.data = d

    class _T:
        def __init__(self, name):
            self.name, self._ins, self._cols = name, None, ""

        def select(self, cols="", *a, **k):
            self._cols = cols
            return self

        def insert(self, row):
            self._ins = dict(row, id="row1")
            return self

        def __getattr__(self, _):
            return lambda *a, **k: self

        def execute(self):
            if self._ins is not None:
                return _R([self._ins])
            if self.name == "businesses":
                if "udyam_number" in self._cols:
                    return _R([{"udyam_number": udyam, "shop_address": "1 Bazaar St"}])
                return _R([dict(BIZ)])
            if self.name == "clients":
                return _R([dict(CLIENT)])
            if self.name == "bills":
                return _R([bill])
            return _R([])

    class _DB:
        def table(self, name):
            return _T(name)

    return _DB()


@pytest.fixture
def _patch(monkeypatch):
    def go(days, amount, udyam=None):
        db = _fake(days, amount, udyam)
        monkeypatch.setattr(admin, "require_db", lambda: db)
        monkeypatch.setattr(admin, "_today", lambda: TODAY)
        return db
    return go


def _html(resp):
    assert resp.status_code == 200
    text = resp.body.decode("utf-8", "replace")
    assert "Internal Server Error" not in text
    return text


def test_the_batch_column_is_one_chip_not_three_controls(_patch):
    _patch(10, 5000)
    t = _html(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert 'class="bchip"' in t
    assert 'id="bpop"' in t                  # the one picker
    # The four controls that used to fight each other are gone.
    assert 'class="bsel"' not in t
    assert 'id="bulkbatch"' not in t
    assert "assignChecked" not in t
    # Bulk is now "everything the search box is showing".
    assert "all shown" in t


def test_a_notice_worthy_party_gets_the_card_and_the_draft(_patch):
    _patch(OVERDUE_WINDOW_DAYS + 60, 48000)
    t = _html(maybe_await(admin.admin_party(token="tok", client_id="c1",
                                            lang="english")))
    assert "Notice worth sending" in t
    assert 'id="noticemodal"' in t and 'id="stepsmodal"' in t
    assert "DRAFT" in t
    assert "Send final WhatsApp demand" in t
    # No Udyam saved -> the card says so and links to Settings.
    assert "no Udyam number saved" in t


def test_a_udyam_shop_sees_the_msme_route_instead(_patch):
    _patch(OVERDUE_WINDOW_DAYS + 60, 48000, udyam="UDYAM-TN-00-1234567")
    t = _html(maybe_await(admin.admin_party(token="tok", client_id="c1",
                                            lang="english")))
    assert "odr.msme.gov.in" in t
    assert "no Udyam number saved" not in t


def test_a_time_barred_party_is_told_to_write_it_off_not_to_send_a_notice(_patch):
    _patch(legal.LIMITATION_DAYS + 100, 48000)
    t = _html(maybe_await(admin.admin_party(token="tok", client_id="c1",
                                            lang="english")))
    assert "Time barred" in t and "Write it off in Tally" in t
    assert 'id="noticemodal"' not in t       # drafting one would be a waste
    assert "Send final WhatsApp demand" not in t


def test_a_party_still_in_the_cadence_sees_no_legal_card(_patch):
    _patch(30, 48000)
    t = _html(maybe_await(admin.admin_party(token="tok", client_id="c1",
                                            lang="english")))
    for gone in ("Notice worth sending", "Time barred", "Act before"):
        assert gone not in t


def test_settings_asks_for_the_udyam_number(_patch):
    _patch(10, 5000)
    t = _html(maybe_await(admin.admin_accounts(token="tok", lang="english")))
    assert 'id="udyam"' in t and 'id="addr"' in t
    assert "udyamregistration.gov.in" in t
    assert "odr.msme.gov.in" in t


def test_the_dashboard_can_sort_by_who_is_running_out_of_time(_patch):
    """74 parties past the cadence cannot be triaged by scrolling, and sorting
    by days overdue surfaces the DEAD ones first. Each row carries its
    limitation runway so one sort puts the soonest-to-expire on top."""
    _patch(3 * 365 - 40, 29150)
    t = _html(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert 'data-rw="40"' in t
    assert 'value="legal"' in t
    assert "running out of time first" in t


def test_a_party_inside_the_cadence_sorts_to_the_bottom(_patch):
    _patch(10, 29150)
    t = _html(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert 'data-rw="99999"' in t


def test_a_time_barred_party_sorts_to_the_very_top(_patch):
    """Nothing can be done about it, but it is the first thing to clear out of
    the books, so it must not hide at the bottom of a 174-row table."""
    _patch(3 * 365 + 120, 84314)
    t = _html(maybe_await(admin.admin_page(token="tok", lang="english")))
    assert 'data-rw="-1"' in t
