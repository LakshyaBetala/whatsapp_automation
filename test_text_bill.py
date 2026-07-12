"""Tests for the text bill parser: BILL <party> <amount> [phone] [days]."""
from decimal import Decimal

from app.services.bot import _parse_text_bill


def test_name_amount_phone():
    assert _parse_text_bill("Ramesh Traders 12500 9876543210") == (
        "Ramesh Traders", Decimal("12500"), "919876543210", None)


def test_name_amount_only():
    assert _parse_text_bill("Ramesh Traders 12500") == (
        "Ramesh Traders", Decimal("12500"), None, None)


def test_indian_grouping_and_rupee_sign():
    assert _parse_text_bill("SK Chemicals 1,73,632") == (
        "SK Chemicals", Decimal("173632"), None, None)
    assert _parse_text_bill("SK Chemicals ₹5000") == (
        "SK Chemicals", Decimal("5000"), None, None)


def test_phone_normalised_from_10_digits():
    name, amt, phone, days = _parse_text_bill("Ram 500 9812345678")
    assert phone == "919812345678"
    assert days is None


def test_credit_days_plain():
    assert _parse_text_bill("Ramesh Traders 12500 45") == (
        "Ramesh Traders", Decimal("12500"), None, 45)


def test_credit_days_with_suffix():
    assert _parse_text_bill("Ramesh 12500 45D") == (
        "Ramesh", Decimal("12500"), None, 45)
    assert _parse_text_bill("Ramesh 12500 60din") == (
        "Ramesh", Decimal("12500"), None, 60)


def test_credit_days_and_phone_any_order():
    assert _parse_text_bill("Ram 12500 9876543210 45") == (
        "Ram", Decimal("12500"), "919876543210", 45)
    assert _parse_text_bill("Ram 12500 45 9876543210") == (
        "Ram", Decimal("12500"), "919876543210", 45)


def test_small_lone_number_is_amount_not_days():
    # 'BILL Ramesh 300' must be a 300-rupee bill, never 300 days credit.
    assert _parse_text_bill("Ramesh 300") == ("Ramesh", Decimal("300"), None, None)
    assert _parse_text_bill("Ramesh Traders 300") == (
        "Ramesh Traders", Decimal("300"), None, None)


def test_days_out_of_range_not_taken():
    # 500 > 365, so it's not credit days; with 12500 before it, it can't be
    # the amount either - the parser keeps it in the name? No: trailing token
    # not matching anything means amount = last token = 500... so this parses
    # as amount 500 with '12500' inside the name. Degenerate input, but must
    # not crash and must not invent a credit period.
    parsed = _parse_text_bill("Ramesh 12500 500")
    assert parsed is not None
    assert parsed[3] is None  # never days


def test_rejects_zero_and_missing_amount():
    assert _parse_text_bill("Ram 0") is None       # non-positive amount
    assert _parse_text_bill("Ramesh") is None      # no amount at all
    assert _parse_text_bill("") is None
    assert _parse_text_bill("Ram notanumber") is None
