"""The website never advertises an expired free pilot and shows real prices."""
import re
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app import site

SRC = open(site.__file__, encoding="utf-8").read()


def test_no_expired_pilot_dates_anywhere():
    assert not re.search(r"(15|30) September", SRC)
    assert "free pilot" not in SRC.lower() and "open pilot" not in SRC.lower()


def test_pricing_shows_the_three_plans_and_the_trial():
    for s in ("Lite", "Collect", "Recover", "Rs 699", "Rs 999", "Rs 1,499",
              "7-day free trial", "150 active debtors", "400 active debtors",
              "1,000 active debtors"):
        assert s in SRC, s


def test_no_dashes_in_copy():
    assert "—" not in SRC and "–" not in SRC
