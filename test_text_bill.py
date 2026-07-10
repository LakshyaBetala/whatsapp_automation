"""Tests for the text bill parser: BILL <party> <amount> [phone]."""
from decimal import Decimal

from app.services.bot import _parse_text_bill


def test_name_amount_phone():
    assert _parse_text_bill("Ramesh Traders 12500 9876543210") == (
        "Ramesh Traders", Decimal("12500"), "919876543210")


def test_name_amount_only():
    assert _parse_text_bill("Ramesh Traders 12500") == (
        "Ramesh Traders", Decimal("12500"), None)


def test_indian_grouping_and_rupee_sign():
    assert _parse_text_bill("SK Chemicals 1,73,632") == (
        "SK Chemicals", Decimal("173632"), None)
    assert _parse_text_bill("SK Chemicals ₹5000") == (
        "SK Chemicals", Decimal("5000"), None)


def test_phone_normalised_from_10_digits():
    name, amt, phone = _parse_text_bill("Ram 500 9812345678")
    assert phone == "919812345678"


def test_rejects_zero_and_missing_amount():
    assert _parse_text_bill("Ram 0") is None       # non-positive amount
    assert _parse_text_bill("Ramesh") is None       # no amount at all
    assert _parse_text_bill("") is None
    assert _parse_text_bill("Ram notanumber") is None
