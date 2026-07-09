"""Slice A: reminder style presets + tone-aware template rendering.

Pure functions only — no DB. Covers the style->cadence map and the
render() tone selection / language-first fallback order.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs.reminder_sweep import STYLE_CADENCE, DEFAULT_CADENCE
from app.services.templates import render
from app.models import Lang


_PARAMS = dict(
    client="Ram", business="RTC", invoice_number="INV1",
    outstanding="₹100", days_overdue="0", upi_link="upi://x",
)


def test_style_cadence_mapping():
    assert STYLE_CADENCE["standard"] == DEFAULT_CADENCE
    assert STYLE_CADENCE["gentle"] == [7, 15, 30]
    assert STYLE_CADENCE["firm"][0] == 2
    # firm is the most frequent, gentle the least
    assert len(STYLE_CADENCE["firm"]) > len(STYLE_CADENCE["gentle"])


def test_standard_uses_base_template():
    name, body = render("reminder", Lang.hi, style="standard", **_PARAMS)
    assert name == "reminder_hi"
    assert "Ram" in body


def test_gentle_and_firm_pick_distinct_variants():
    name_g, gentle = render("reminder", Lang.hi, style="gentle", **_PARAMS)
    name_f, firm = render("reminder", Lang.hi, style="firm", **_PARAMS)
    assert name_g == "reminder_gentle_hi"
    assert name_f == "reminder_firm_hi"
    assert gentle != firm
    assert "Ram" in gentle and "Ram" in firm


def test_overdue_tone_variants_exist():
    name_g, _ = render("overdue", Lang.hi, style="gentle", **_PARAMS)
    name_f, _ = render("overdue", Lang.hi, style="firm", **_PARAMS)
    assert name_g == "overdue_gentle_hi"
    assert name_f == "overdue_firm_hi"


def test_unknown_style_falls_back_to_base():
    name, _ = render("reminder", Lang.hi, style="weird", **_PARAMS)
    assert name == "reminder_hi"


def test_language_beats_tone_in_fallback():
    # No Gujarati 'gentle' variant is authored -> must use the Gujarati BASE
    # template, never the Hindi gentle one.
    name, _ = render("reminder", Lang.gu, style="gentle", **_PARAMS)
    assert name == "reminder_gu"
