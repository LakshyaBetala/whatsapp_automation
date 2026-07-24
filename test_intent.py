"""Intent classifier - the pure verdict-shaping (no network).

Covers _parse (enum guard, amount coercion, confidence clamp, the "promise needs
a date" rule), _clean_date (future-only), is_configured, and that classify()
degrades to None without a key or on empty text.
"""
import asyncio
import datetime
import json

from app.services import intent

TODAY = datetime.date(2026, 7, 25)


def _gem(obj) -> dict:
    """Wrap a dict as Gemini's generateContent response shape."""
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


# ── _parse ────────────────────────────────────────────────────────────────
def test_paid_claim_with_amount():
    v = intent._parse(_gem({"intent": "paid_claim", "amount": 20000, "confidence": 0.9}), TODAY)
    assert v == {"intent": "paid_claim", "amount": 20000.0, "promise_date": None, "confidence": 0.9}


def test_promise_with_future_date():
    v = intent._parse(_gem({"intent": "promise", "promise_date": "2026-08-05", "confidence": 0.8}), TODAY)
    assert v["intent"] == "promise" and v["promise_date"] == "2026-08-05"


def test_dateless_promise_downgrades_to_unclear():
    v = intent._parse(_gem({"intent": "promise", "confidence": 0.95}), TODAY)
    assert v["intent"] == "unclear" and v["confidence"] <= 0.5


def test_past_promise_date_is_dropped():
    v = intent._parse(_gem({"intent": "promise", "promise_date": "2020-01-01", "confidence": 0.9}), TODAY)
    # date in the past is a misread -> dropped -> promise with no date -> unclear
    assert v["promise_date"] is None and v["intent"] == "unclear"


def test_unknown_intent_becomes_unclear():
    v = intent._parse(_gem({"intent": "banana", "confidence": 0.9}), TODAY)
    assert v["intent"] == "unclear"


def test_confidence_clamped():
    assert intent._parse(_gem({"intent": "chatter", "confidence": 5}), TODAY)["confidence"] == 1.0
    assert intent._parse(_gem({"intent": "chatter", "confidence": -2}), TODAY)["confidence"] == 0.0


def test_zero_or_negative_amount_dropped():
    assert intent._parse(_gem({"intent": "paid_claim", "amount": 0, "confidence": 0.9}), TODAY)["amount"] is None


def test_bad_json_returns_none():
    assert intent._parse({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}, TODAY) is None
    assert intent._parse({}, TODAY) is None
    assert intent._parse(_gem([1, 2, 3]), TODAY) is None   # not a dict


# ── _clean_date ─────────────────────────────────────────────────────────────
def test_clean_date():
    assert intent._clean_date("2026-08-05", TODAY) == "2026-08-05"
    assert intent._clean_date("2026-07-25", TODAY) == "2026-07-25"   # today is allowed
    assert intent._clean_date("2024-01-01", TODAY) is None           # past
    assert intent._clean_date("garbage", TODAY) is None
    assert intent._clean_date(None, TODAY) is None


# ── configuration / degradation ─────────────────────────────────────────────
def test_is_configured(monkeypatch):
    monkeypatch.setattr(intent.settings, "gemini_api_key", "")
    assert intent.is_configured() is False
    monkeypatch.setattr(intent.settings, "gemini_api_key", "key")
    assert intent.is_configured() is True


def test_classify_none_without_key(monkeypatch):
    monkeypatch.setattr(intent.settings, "gemini_api_key", "")
    assert asyncio.run(intent.classify("paisa bhej diya")) is None


def test_classify_none_on_empty_text(monkeypatch):
    monkeypatch.setattr(intent.settings, "gemini_api_key", "key")
    assert asyncio.run(intent.classify("   ")) is None   # empty -> no network, None
