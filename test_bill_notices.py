"""New bills reach the customer 5-10 minutes after sync: merged, quiet at
night, at most one bill message per customer per 30 minutes."""
import asyncio
import datetime as dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bill_notices as bn

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(h, m=0, day=20):
    return dt.datetime(2026, 10, day, h, m, tzinfo=IST)


def test_a_bill_at_8pm_goes_by_8_10pm():
    assert bn.next_send_time(at(20, 0)) == at(20, 10)


def test_another_bill_extends_the_window_but_never_past_30_minutes():
    # first bill 20:00, second 20:08 -> 20:18; fifth at 20:27 -> capped at 20:30
    assert bn.next_send_time(at(20, 8), first_at=at(20, 0)) == at(20, 18)
    assert bn.next_send_time(at(20, 27), first_at=at(20, 0)) == at(20, 30)


def test_at_most_one_bill_message_per_customer_per_30_minutes():
    assert bn.next_send_time(at(15, 0), last_sent_at=at(14, 55)) == at(15, 25)


def test_quiet_hours_wait_for_8am():
    assert bn.next_send_time(at(23, 0)) == at(8, 0, day=21)
    assert bn.next_send_time(at(21, 55)) == at(8, 0, day=21)     # 22:05 is quiet
    assert bn.next_send_time(at(6, 30)) == at(8, 0)


def test_only_fresh_bills_go_out():
    today = dt.date(2026, 10, 20)
    assert bn.is_fresh(dt.date(2026, 10, 18), today) is True
    assert bn.is_fresh(dt.date(2026, 10, 17), today) is False   # old FY replay stays quiet


BILLS = [{"invoice_number": "INV-71", "invoice_date": "2026-10-20", "due_date": "2026-11-19", "amount": 12500},
         {"invoice_number": "INV-72", "invoice_date": "2026-10-20", "due_date": "2026-11-19", "amount": 3400.5}]


def test_compose_english_lists_each_bill_total_and_link():
    msg = bn.compose("Ramesh Traders", "Shanti Electricals", BILLS, 48250,
                     en=True, pay_link="https://app.tryasva.com/pay/abc")
    assert "Dear Ramesh Traders" in msg and "Shanti Electricals" in msg
    assert "INV-71" in msg and "INV-72" in msg and "19 Nov" in msg
    assert "48,250" in msg and "https://app.tryasva.com/pay/abc" in msg
    assert "—" not in msg and "–" not in msg


def test_bill_notice_is_english_even_for_a_hinglish_batch():
    """A bill is a document, so the founder's rule is strict English for it,
    whatever language the party's reminder batch uses."""
    msg = bn.compose("Ramesh", "Shanti", BILLS[:1], 12500, en=False, pay_link=None)
    assert "Dear Ramesh" in msg and "INV-71" in msg and "Total outstanding now" in msg
    for hinglish in ("Namaste", "Kul baaki", "Dhanyavaad", "naya bill"):
        assert hinglish not in msg
    assert "http" not in msg


def test_compose_caps_a_long_list():
    many = [dict(BILLS[0], invoice_number=f"INV-{i}") for i in range(9)]
    msg = bn.compose("R", "S", many, 1, en=True, pay_link=None)
    assert "INV-5" in msg and "INV-6" not in msg and "and 3 more" in msg
