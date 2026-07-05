"""UPI payment links and QR codes for reminders/invoices.

A tappable upi:// link opens GPay/PhonePe/Paytm pre-filled; the QR image
covers customers who receive the message on a different phone than the
one they pay from (very common in shops — owner's phone gets the bill,
accountant scans the QR).
"""
from __future__ import annotations

import base64
import io
import logging
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

log = logging.getLogger(__name__)


def upi_link(vpa: str, payee_name: str = "", amount: Decimal | float | None = None,
             note: str = "") -> str:
    """Build a upi://pay deep link. Amount/note optional."""
    parts = [f"pa={quote(vpa)}"]
    if payee_name:
        parts.append(f"pn={quote(payee_name[:50])}")
    if amount:
        parts.append(f"am={float(amount):.2f}")
    if note:
        parts.append(f"tn={quote(note[:50])}")
    parts.append("cu=INR")
    return "upi://pay?" + "&".join(parts)


def qr_png_base64(data: str) -> Optional[str]:
    """Render `data` as a QR PNG, base64-encoded. None if qrcode/pillow
    missing — callers degrade to the text link."""
    try:
        import qrcode
    except ImportError:
        log.warning("qrcode library not installed — sending text link only")
        return None
    try:
        img = qrcode.make(data, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        log.exception("QR generation failed")
        return None
