"""Preset rules: settle the common customer replies with no AI call.

Returns the same verdict shape as intent.classify(), plus source="rule", or
None to hand the message to the model. A rule fires only on a short,
single-purpose message; anything long, mixed, negated, or carrying a number
(a date, an amount) is for the model. Payment-app share texts and business
auto-greetings are the exceptions: long, but unmistakable.

Languages: English, Hindi / Hinglish, Tamil / Tanglish, Gujarati, Marathi,
in Latin letters plus the most common native-script words.
"""
from __future__ import annotations

import re

MAX_WORDS = 12

# Automatic replies from the customer's own WhatsApp Business (greeting / away).
_AUTO_GREETING = re.compile(
    r"\bwelcome to\b"
    r"|\bthank(s| you) for (contacting|reaching out|reaching|messaging|your message)\b"
    r"|\bwe(['’]| a)re (currently )?(away|closed|unavailable|out of office)\b"
    r"|\bi(['’]| ha)ve connected you\b"
    r"|\bthis is an automated\b"
    r"|\bhow (can|may) (we|i) (help|assist)\b",
    re.I)
# Share text a payment app produces ("Paid using Google Pay", "Payment successful").
_PAY_APP = re.compile(
    r"\b(paid|sent|payment successful|transaction successful)\b.{0,40}"
    r"\b(google pay|gpay|phonepe|paytm|bhim|upi)\b", re.I)
_RUPEES = re.compile(r"(?:₹|\brs\.?|\binr)\s?([\d,]+(?:\.\d+)?)", re.I)
_NEGATION = re.compile(r"\b(not|no|nahi|nahin|nai|illa|illai|won'?t|cannot|can'?t)\b", re.I)
_DIGIT = re.compile(r"\d")

# Checked before the negation guard: these phrases contain "no" / "not".
_DISPUTE = re.compile(
    r"\bnill? balance\b|\bno (dues|balance)\b|\bnothing (is )?pending\b"
    r"|\bnot (our|ours|mine|my)\b|\bwrong (bill|amount)\b|\bbill galat\b|\bgalat bill\b"
    r"|\b(hamara|mera) nahi\b", re.I)

_RULES: list[tuple[str, re.Pattern]] = [
    ("paid_claim", re.compile(
        r"^(paid|payment (done|sent|made|completed)|done|sent|transferred"
        r"|paisa bhej diya|bhej diya|kar diya|ho gaya|payment ho gaya|de diya"
        r"|kattiten|kattitten|anupitten|anuppitten|pathavle|moklavi didha"
        r"|already paid|all payment paid|full payment done|payment cleared)\b", re.I)),
    ("balance_query", re.compile(
        r"\b(kitna|kitne|baki|baaki|bakaya|balance|outstanding|pending amount|how much"
        r"|evlo|evvalavu|evvlo|ketla|kiti)\b|बाकी|कितना|எவ்வளவு|பாக்கி", re.I)),
    ("statement_request", re.compile(
        r"\b(statement|ledger|hisab|hisaab|khata|account copy|soa)\b", re.I)),
    ("bill_request", re.compile(
        r"\b(bill|invoice|bil)\b.*\b(bhejo|bhej do|send|copy|anuppu|anupunga|please|pls|do)\b"
        r"|\b(send|share)\b.*\b(bill|invoice)\b", re.I)),
    ("payment_info", re.compile(
        r"\b(upi|account (no|number|details)|bank details|ifsc|qr|gpay number|phonepe number"
        r"|kaise pay|how to pay|payment details)\b", re.I)),
    ("chatter", re.compile(
        r"^(ok|okay|okk|k|thanks|thank you|thanku|noted|sure|fine|haan|ha|ji|theek hai|thik hai"
        r"|sari|saringa|nandri|dhanyavad|dhanyavaad|aabhar|barabar|good morning|gm|hi|hello"
        r"|👍|🙏|சரி|சரிங்க|நன்றி|धन्यवाद|ठीक है)( sir| ji| bhai| anna)?[.!]*$", re.I)),
]


def _verdict(intent: str, amount=None) -> dict:
    return {"intent": intent, "amount": amount, "promise_date": None,
            "confidence": 1.0, "source": "rule"}


def _rupees(text: str):
    m = _RUPEES.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def match(text: str) -> dict | None:
    t = (text or "").strip()
    if not t:
        return None
    if _AUTO_GREETING.search(t):
        return _verdict("auto_greeting")
    if _PAY_APP.search(t):
        return _verdict("paid_claim", _rupees(t))
    if len(t.split()) > MAX_WORDS:
        return None
    if _DISPUTE.search(t):
        return _verdict("dispute")
    if _NEGATION.search(t) or _DIGIT.search(t):
        return None
    hits = [name for name, rx in _RULES if rx.search(t)]
    return _verdict(hits[0]) if len(hits) == 1 else None
