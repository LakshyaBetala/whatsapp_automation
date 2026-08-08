"""Photo-bill OCR - extract party/phone/amount from a photographed bill.

Uses Gemini (free tier) via the REST API with a JSON response schema, so
no SDK dependency and no cost during the pilot. Requires GEMINI_API_KEY
in .env (free key from aistudio.google.com). Without it the bot tells
the owner the feature is not configured; everything else keeps working.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


class BillExtract(BaseModel):
    party_name: Optional[str] = None    # customer/buyer name on the bill
    phone: Optional[str] = None         # customer phone if printed/written
    amount: Optional[float] = None      # grand total payable
    bill_number: Optional[str] = None
    bill_date: Optional[str] = None     # YYYY-MM-DD if readable
    readable: bool = True               # False if the photo is too unclear


PROMPT = """This is a photo of a handwritten or printed bill/invoice from an
Indian wholesale shop. Extract:
- party_name: the CUSTOMER the bill is made out to (not the shop issuing it)
- phone: the customer's phone number if visible (10-digit Indian mobile)
- amount: the grand total payable in rupees (numeric only, no commas)
- bill_number: bill/invoice number if present
- bill_date: date in YYYY-MM-DD format if readable
Set readable=false only if the image is too blurry or dark to extract anything.
If a field is not present or not legible, use null. Never guess values."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "party_name": {"type": "STRING", "nullable": True},
        "phone": {"type": "STRING", "nullable": True},
        "amount": {"type": "NUMBER", "nullable": True},
        "bill_number": {"type": "STRING", "nullable": True},
        "bill_date": {"type": "STRING", "nullable": True},
        "readable": {"type": "BOOLEAN"},
    },
    "required": ["readable"],
}


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


async def extract_bill(image_b64: str, media_type: str = "image/jpeg") -> Optional[BillExtract]:
    """Run vision extraction on a bill photo. Returns None on failure."""
    if not is_configured():
        return None
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": image_b64}},
                {"text": PROMPT},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(
                GEMINI_URL,
                params={"key": settings.gemini_api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return BillExtract(**json.loads(text))
    except Exception:
        log.exception("Bill OCR failed")
        return None


class PaymentExtract(BaseModel):
    amount: Optional[float] = None       # rupees paid
    payer: Optional[str] = None          # name on the UPI/bank screenshot
    ref: Optional[str] = None            # UPI reference / UTR / txn id (proof + dedupe key)
    confidence: float = 0.0              # 0-1 how sure the read is (drives auto-prefill vs "please check")


_PAY_PROMPT = (
    "This is a screenshot a customer sent to prove they paid (a UPI app like "
    "GPay/PhonePe/Paytm, a bank transfer, or a cheque). Extract as JSON:\n"
    "- amount: the RUPEE amount paid (number only, no commas/symbols; null if unclear)\n"
    "- payer: the name of the person/firm who PAID (the sender), or null\n"
    "- ref: the UPI reference number / UTR / transaction id (digits, often 12), or null\n"
    "- confidence: 0.0-1.0, how sure you are of the amount (1 = crisp and certain, "
    "low = blurry/ambiguous)\n"
    "Return only these fields.")
_PAY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "amount": {"type": "NUMBER", "nullable": True},
        "payer": {"type": "STRING", "nullable": True},
        "ref": {"type": "STRING", "nullable": True},
        "confidence": {"type": "NUMBER"},
    },
}


async def extract_payment(image_b64: str, media_type: str = "image/jpeg") -> Optional[PaymentExtract]:
    """Read a payment screenshot: amount + payer + UPI reference + a confidence
    score. Returns None if not configured / unreadable. The confidence is a
    SUGGESTION for the app (high -> prefill the amount; low -> ask the owner to
    check). ASVA never posts to Tally on this alone - the owner always confirms."""
    if not is_configured():
        return None
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": image_b64}},
                {"text": _PAY_PROMPT},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _PAY_SCHEMA,
            "temperature": 0,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(GEMINI_URL, params={"key": settings.gemini_api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        raw = json.loads(text)
        amt = raw.get("amount")
        amt = float(amt) if amt is not None else None
        return PaymentExtract(
            amount=amt if (amt and amt > 0) else None,
            payer=(raw.get("payer") or None),
            ref=(str(raw.get("ref")).strip() or None) if raw.get("ref") else None,
            confidence=float(raw.get("confidence") or 0.0),
        )
    except Exception:
        log.exception("Payment-screenshot OCR failed")
        return None


async def extract_payment_amount(image_b64: str, media_type: str = "image/jpeg") -> Optional[float]:
    """Back-compat thin wrapper: just the amount (used by the reply pipeline)."""
    r = await extract_payment(image_b64, media_type)
    return r.amount if r else None
