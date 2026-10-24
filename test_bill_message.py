"""The bill a customer receives is English, and its pay link is tappable.

Two faults, both visible in the message owners were actually sending:

  * `lang = Lang(client.get("language") or "hi")` defaulted every customer with
    no language of their own - nearly all of them - to the Hinglish template,
    whatever the shop had chosen. The standing rule is English unless the owner
    picks Hinglish.
  * `upi_link=biz.get("upi_vpa")` put the bare VPA in the message, so the bill
    read "Pay via UPI: shop@ybl". That is not a link and not payable; the
    customer has to retype it into an app. Reminders moved to the signed /pay
    page long ago and the bill never followed.
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.models import Lang
from app.services.templates import render


def _bill(**over) -> str:
    params = {
        "client": "Ramesh Traders", "business": "Supreme Traders",
        "invoice_number": "2526RTC0203", "amount": "Rs 12,500",
        "date": "20-10-2026", "pdf_url": "",
        "upi_link": "https://app.tryasva.com/pay/abc123",
    }
    params.update(over)
    return render("invoice_en", Lang.hi, **params)[1]


# ── the message itself ────────────────────────────────────────────────────
def test_the_english_bill_has_no_hinglish_left_in_it():
    body = _bill()
    for word in ("Namaste", "Dhanyavaad", "karein", "taraf se", " ji,"):
        assert word not in body, f"{word!r} is still in the English bill"


def test_it_offers_a_tappable_page_not_a_bare_upi_id():
    body = _bill()
    assert "https://app.tryasva.com/pay/" in body
    assert "Pay via UPI:" not in body, "the old bare-VPA wording is back"
    assert "@ybl" not in body and "@okhdfcbank" not in body


def test_the_link_sits_on_its_own_line():
    """WhatsApp stops linkifying a URL that shares a line with other text once
    the message wraps, which is how a pay link stops being tappable."""
    body = _bill()
    assert "\nhttps://app.tryasva.com/pay/abc123\n" in body


def test_it_still_names_the_bill_and_the_amount():
    body = _bill()
    assert "2526RTC0203" in body and "Rs 12,500" in body
    assert "Supreme Traders" in body and "Ramesh Traders" in body


def test_no_dashes_in_customer_copy():
    """House rule: never an em or en dash in anything a customer reads."""
    assert "—" not in _bill() and "–" not in _bill()


# ── the decision that picks it ────────────────────────────────────────────
def test_the_bill_no_longer_defaults_every_customer_to_hinglish():
    from app.routers import bills
    src = inspect.getsource(bills._generate_and_deliver)
    assert 'Lang(client.get("language") or "hi")' not in src, \
        "the Hinglish-by-default line is back"
    assert 'str(biz.get("msg_language") or "").lower() != "hinglish"' in src, \
        "language must follow the shop's own setting"


def test_an_explicitly_chosen_regional_language_still_wins():
    """Gujarati and Marathi were set on purpose; English-first must not
    silently overwrite a deliberate choice."""
    from app.routers import bills
    src = inspect.getsource(bills._generate_and_deliver)
    assert '_cl in ("gu", "mr")' in src


def test_the_bill_sends_the_pay_page_not_the_vpa():
    from app.routers import bills
    src = inspect.getsource(bills._generate_and_deliver)
    assert 'upi_link=biz.get("upi_vpa")' not in src, "the bare VPA is back"
    assert "upi_link=bill_pay_link" in src
    assert "pay_page_url" in src


def test_the_hinglish_bill_is_still_available_for_shops_that_chose_it():
    body = render("invoice", Lang.hi, client="Ramesh", business="Supreme",
                  invoice_number="1", amount="Rs 100", date="20-10-2026",
                  upi_link="https://app.tryasva.com/pay/x", pdf_url="")[1]
    assert "Namaste" in body, "the Hinglish template must stay for shops that picked it"
