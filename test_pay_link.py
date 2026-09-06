"""The pay link and QR are the product's core loop.

Customers reported "the link does not work, even the QR". Two causes, both here:
  1. the VPA's "@" was percent-encoded, so UPI apps read the address literally
     and rejected it. The QR carries the same string, so both broke together.
  2. WhatsApp does not linkify "upi://", so the link arrived as dead grey text.
"""
from decimal import Decimal

import pytest

from app.services import upi

VPA = "rupeshrtc-1@okhdfcbank"
BIZ = "94e2e0a7-2cc9-4bd4-b5a2-eca6ce924d17"


# ── the deep link / QR payload ───────────────────────────────────────────
def test_vpa_keeps_its_at_sign():
    # %40 is what broke every UPI app. Bank-issued QRs always keep "@" raw.
    link = upi.upi_link(VPA, "RISHAB TRADING COMPANY", Decimal("9475.00"), "INV1")
    assert "pa=" + VPA in link
    assert "%40" not in link


def test_link_carries_payee_amount_and_currency():
    link = upi.upi_link(VPA, "RISHAB TRADING COMPANY", Decimal("9475.00"), "INV1")
    assert "am=9475.00" in link
    assert "cu=INR" in link
    assert link.startswith("upi://pay?")


def test_ampersand_in_shop_name_cannot_truncate_the_link():
    # "M&M" used to inject a parameter separator, silently dropping am= and cu=.
    link = upi.upi_link(VPA, "M&M Traders", Decimal("100"), "bill#7")
    assert link.count("&") == len(link.split("&")) - 1
    assert "am=100.00" in link and "cu=INR" in link
    assert "&M" not in link.split("pn=")[1].split("&")[0] + "x"


def test_spaces_are_encoded_but_not_the_at():
    link = upi.upi_link(VPA, "A B", Decimal("1"), "n")
    assert "pn=A%20B" in link
    assert "%40" not in link


# ── the signed token behind the tappable link ────────────────────────────
def test_token_round_trips_business_and_amount():
    t = upi.pay_token(BIZ, Decimal("9475.00"))
    assert upi.parse_pay_token(t) == (BIZ, Decimal("9475.00"))


def test_token_survives_paise():
    t = upi.pay_token(BIZ, Decimal("1234.56"))
    assert upi.parse_pay_token(t) == (BIZ, Decimal("1234.56"))


def test_tampered_token_is_rejected():
    t = upi.pay_token(BIZ, Decimal("100"))
    flipped = t[:-3] + ("AAA" if not t.endswith("AAA") else "BBB")
    assert upi.parse_pay_token(flipped) is None


def test_a_token_cannot_be_edited_to_another_amount():
    # Without the signature a customer could rewrite what they owe. Decode a
    # real token, change ONLY the amount bytes, re-encode, and it must be
    # rejected because the signature no longer matches the body.
    good = upi.pay_token(BIZ, Decimal("9475.00"))
    blob = upi._unb64u(good)
    body, sig = blob[:20], blob[20:]
    forged = body[:16] + (1).to_bytes(4, "big") + sig      # now claims 1 paisa
    assert upi.parse_pay_token(upi._b64u(forged)) is None


@pytest.mark.parametrize("junk", ["", "xx", "!!!!", "a" * 200])
def test_junk_tokens_never_raise(junk):
    assert upi.parse_pay_token(junk) is None


def test_token_stays_short_enough_for_a_whatsapp_message():
    assert len(upi.pay_token(BIZ, Decimal("9475.00"))) <= 40


# ── the link that actually goes in the message ───────────────────────────
def test_pay_page_url_is_https_so_whatsapp_makes_it_tappable(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://app.tryasva.com")
    url = upi.pay_page_url(BIZ, Decimal("9475.00"), "2526RTC0071")
    assert url.startswith("https://app.tryasva.com/pay/")
    assert not url.startswith("upi://")
    assert "ref=2526RTC0071" in url


def test_pay_page_url_survives_a_reference_with_spaces(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://app.tryasva.com")
    url = upi.pay_page_url(BIZ, Decimal("5"), "3 bills")
    assert " " not in url
