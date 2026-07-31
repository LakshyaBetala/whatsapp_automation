"""Classify a customer's WhatsApp reply to a payment reminder.

Given a short, messy, multilingual message ("paisa bhej diya", "5 tareek ko",
"already paid last week", "bill galat hai"), return a structured verdict:
intent + amount + promise_date + confidence. Uses Gemini (free tier) over the
same REST pattern as ocr.py - no SDK, no cost during the pilot.

Graceful degrade: without GEMINI_API_KEY, or on any API failure, classify()
returns None. The caller (replies.py) then falls back to its keyword fast-path
and, for anything it cannot read, forwards the message to the owner - it never
auto-acts on a message it did not understand.

The network call and the parsing are split on purpose: _parse() is a pure
function so the whole verdict-shaping (enum guarding, date validation, the
"promise needs a date" rule) is unit-tested without touching the network.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
INTENTS = {"paid_claim", "promise", "dispute", "chatter", "unclear"}
# Transient statuses worth a retry. 429 = free-tier rate limit (a burst of
# replies hits this); 5xx = Gemini hiccup. Everything else (400 bad request,
# 403 bad key) is not retried - it will not fix itself.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING",
                   "enum": ["paid_claim", "promise", "dispute", "chatter", "unclear"]},
        "amount": {"type": "NUMBER", "nullable": True},
        "promise_date": {"type": "STRING", "nullable": True},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["intent", "confidence"],
}


def _today_ist() -> _dt.date:
    return _dt.datetime.now(IST).date()


def _prompt(today: _dt.date) -> str:
    return (
        "You classify a customer's WhatsApp reply to a payment reminder from an "
        "Indian wholesale shop. The customer may write in English, Hindi, Hinglish, "
        f"Gujarati or Marathi, often romanised. Today is {today.isoformat()} "
        "(Asia/Kolkata).\n\n"
        "Return:\n"
        "- intent, one of:\n"
        "  paid_claim = says they have already paid or are paying now "
        "(paid, done, sent, paisa bhej diya, kar diya, ho gaya)\n"
        "  promise = says they will pay at a FUTURE time "
        "(kal, 5 tareek ko, next week, Monday, 3 din me, agle hafte)\n"
        "  dispute = disagrees, says the bill or amount is wrong, or refuses\n"
        "  chatter = greeting, thanks, or anything not about payment\n"
        "  unclear = about payment but you cannot tell which\n"
        "- amount: the rupee number they mention if any (numeric only), else null\n"
        "- promise_date: for a promise, resolve the date they mean to an absolute "
        "YYYY-MM-DD using today's date; else null\n"
        "- confidence: 0 to 1, how sure you are of the intent\n"
        "Never guess an amount or date that is not implied. If unsure, use unclear "
        "with low confidence."
    )


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def _clean_date(raw, today: _dt.date | None) -> str | None:
    """Accept only a valid YYYY-MM-DD that is today or later (a 'future' date in
    the past is a misread and is dropped)."""
    if not raw:
        return None
    try:
        d = _dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None
    if d < (today or _today_ist()):
        return None
    return d.isoformat()


def _parse(data: dict, today: _dt.date | None = None) -> dict | None:
    """Pure: normalise Gemini's JSON into {intent, amount, promise_date,
    confidence}. Guards the enum, validates the date, coerces the amount, and
    downgrades a dateless 'promise' to 'unclear' (we cannot hold without a date)."""
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    intent = str(obj.get("intent") or "unclear").lower().strip()
    if intent not in INTENTS:
        intent = "unclear"

    amount = obj.get("amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    if amount is not None and amount <= 0:
        amount = None

    promise_date = _clean_date(obj.get("promise_date"), today)

    try:
        conf = float(obj.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = min(max(conf, 0.0), 1.0)

    # A promise with no readable future date cannot be held to a day - treat it
    # as unclear so the owner is looped in rather than a silent open-ended hold.
    if intent == "promise" and not promise_date:
        intent, conf = "unclear", min(conf, 0.5)

    return {"intent": intent, "amount": amount,
            "promise_date": promise_date, "confidence": conf}


async def classify(text: str) -> dict | None:
    """Classify one reply. Returns the verdict dict, or None when Gemini is not
    configured, the text is empty, or the call fails (caller then degrades)."""
    if not is_configured() or not (text or "").strip():
        return None
    today = _today_ist()
    payload = {
        "contents": [{"parts": [{"text": _prompt(today) + "\n\nMessage:\n" + text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
        },
    }
    # Retry transient failures (rate limit / hiccup) with a short backoff so a
    # burst of replies does not silently degrade. A non-transient error (bad key,
    # bad request) stops immediately; after all attempts we degrade to None and
    # the caller forwards the reply to the owner - it never acts on a miss.
    last_err = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                resp = await http.post(GEMINI_URL,
                                       params={"key": settings.gemini_api_key},
                                       json=payload)
            if resp.status_code in _RETRY_STATUS:
                last_err = f"HTTP {resp.status_code}"
            elif resp.status_code >= 400:
                log.warning("intent.classify non-retryable HTTP %s - degrading", resp.status_code)
                return None
            else:
                return _parse(resp.json(), today)
        except Exception as e:                     # timeout / network - transient
            last_err = repr(e)
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(0.6 * (attempt + 1))   # 0.6s, then 1.2s
    log.warning("intent.classify failed after %d attempts (%s) - degrading", _MAX_ATTEMPTS, last_err)
    return None
