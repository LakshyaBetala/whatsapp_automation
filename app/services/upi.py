"""UPI payment links and QR codes for reminders/invoices.

A tappable upi:// link opens GPay/PhonePe/Paytm pre-filled; the QR image
covers customers who receive the message on a different phone than the
one they pay from (very common in shops - owner's phone gets the bill,
accountant scans the QR).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import uuid as _uuid
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

log = logging.getLogger(__name__)


# The VPA must reach the UPI app with its "@" INTACT. quote() escapes it to
# %40, and although that is valid percent-encoding, real UPI apps (PhonePe,
# Paytm, several GPay builds) parse `pa` naively and read the address as the
# literal "shop%40okhdfcbank" -> "invalid UPI ID". Because the QR encodes the
# same string, an escaped "@" breaks the link AND the QR together, which is
# exactly what customers reported. Every bank-issued UPI QR keeps "@" raw.
_VPA_SAFE = "@.-_"


def upi_link(vpa: str, payee_name: str = "", amount: Decimal | float | None = None,
             note: str = "") -> str:
    """Build a upi://pay deep link. Amount/note optional.

    Used for BOTH the deep link and the QR payload, so the encoding here decides
    whether a customer can pay at all.
    """
    parts = [f"pa={quote((vpa or '').strip(), safe=_VPA_SAFE)}"]
    if payee_name:
        parts.append(f"pn={quote(_clean_note(payee_name)[:50], safe=_VPA_SAFE)}")
    if amount:
        parts.append(f"am={float(amount):.2f}")
    if note:
        parts.append(f"tn={quote(_clean_note(note)[:50], safe=_VPA_SAFE)}")
    parts.append("cu=INR")
    return "upi://pay?" + "&".join(parts)


def _clean_note(text: str) -> str:
    """UPI notes/names reject most punctuation; keep letters, digits and spaces.

    A stray "&" or "#" in a shop name silently truncated the deep link at that
    character, so the amount and note were dropped."""
    out = [c for c in (text or "") if c.isalnum() or c in " .-_"]
    return " ".join("".join(out).split())


def qr_png_base64(data: str) -> Optional[str]:
    """Render `data` as a QR PNG, base64-encoded. None if qrcode/pillow
    missing - callers degrade to the text link."""
    try:
        import qrcode
    except ImportError:
        log.warning("qrcode library not installed - sending text link only")
        return None
    try:
        img = qrcode.make(data, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        log.exception("QR generation failed")
        return None


# ── Tappable pay link ────────────────────────────────────────────────────
# WhatsApp only turns http/https into a tappable link. A raw "upi://pay?..."
# arrives as dead grey text the customer cannot press, which is why customers
# reported "the link does not work". We send a short https link to a hosted
# page instead; that page carries the real UPI button, the QR and the bank
# details. The token is SIGNED and self-contained, so a reminder costs no extra
# database write and the URL cannot be edited to point at another shop or a
# different amount.

def _pay_key() -> bytes:
    from app.config import settings
    return hashlib.sha256(("asva-pay-v1:" + (settings.supabase_service_key or "")
                           ).encode("utf-8")).digest()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def pay_token(business_id: str, amount: Decimal | float | None) -> str:
    """Short signed token naming the shop and the amount to collect."""
    try:
        raw = _uuid.UUID(str(business_id)).bytes
    except (ValueError, AttributeError, TypeError):
        # Real business ids are UUIDs; never raise inside message composition
        # over an odd one, or a whole reminder send would fail.
        raw = hashlib.sha256(str(business_id).encode("utf-8")).digest()[:16]
    paise = max(0, min(int(round(float(amount or 0) * 100)), 0xFFFFFFFF))
    body = raw + paise.to_bytes(4, "big")
    return _b64u(body + hmac.new(_pay_key(), body, hashlib.sha256).digest()[:6])


def parse_pay_token(token: str) -> Optional[tuple]:
    """(business_id, amount) from a token, or None if it was tampered with."""
    try:
        blob = _unb64u((token or "").strip())
        if len(blob) != 26:
            return None
        body, sig = blob[:20], blob[20:]
        want = hmac.new(_pay_key(), body, hashlib.sha256).digest()[:6]
        if not hmac.compare_digest(sig, want):
            return None
        return str(_uuid.UUID(bytes=body[:16])), Decimal(
            int.from_bytes(body[16:], "big")) / 100
    except Exception:
        return None


def pay_page_url(business_id: str, amount: Decimal | float | None,
                 ref: str = "") -> str:
    """The https link a customer can actually TAP in WhatsApp.

    Send this instead of the raw upi:// deep link: WhatsApp does not linkify
    custom schemes, so "upi://pay?..." arrives as dead text. The page it opens
    carries the UPI button, the QR and the bank details."""
    from app.config import settings
    base = (settings.public_base_url or "").rstrip("/")
    url = f"{base}/pay/{pay_token(business_id, amount)}"
    if ref:
        url += "?ref=" + quote(str(ref)[:40], safe="")
    return url
