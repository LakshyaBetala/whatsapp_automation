"""Photo-bill OCR — extract party/phone/amount from a photographed bill.

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

GEMINI_MODEL = "gemini-2.0-flash"
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
