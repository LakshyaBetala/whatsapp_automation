"""PDF invoice generation via WeasyPrint.

Renders the Jinja2 HTML template, converts to PDF, uploads to Supabase
Storage, and returns a public URL for AiSensy to attach.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.db import require_db
from app.services.templates import inr

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
BUCKET_NAME = "invoices"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
)


def _ensure_bucket() -> None:
    """Create the Storage bucket if it does not exist yet."""
    db = require_db()
    try:
        db.storage.get_bucket(BUCKET_NAME)
    except Exception:
        try:
            db.storage.create_bucket(
                BUCKET_NAME,
                options={"public": True, "file_size_limit": 5 * 1024 * 1024},
            )
            log.info("Created Supabase Storage bucket: %s", BUCKET_NAME)
        except Exception as exc:
            # Bucket may already exist from a concurrent call — not fatal.
            log.warning("Bucket create returned: %s", exc)


def _format_date(d: date | str | None) -> str:
    """Render a date in Indian DD-MM-YYYY format."""
    if d is None:
        return "—"
    if isinstance(d, str):
        return d
    return d.strftime("%d-%m-%Y")


async def generate_invoice_pdf(
    *,
    business_name: str,
    business_phone: str = "",
    business_address: str = "",
    upi_vpa: str | None = None,
    client_name: str,
    client_address: str = "",
    invoice_number: str,
    invoice_date: date,
    due_date: date,
    credit_days: int = 30,
    amount: Decimal,
    previous_outstanding: Decimal = Decimal(0),
    description: str = "Goods as per invoice",
    bill_id: str = "",
) -> str:
    """Render an invoice PDF, upload it, and return the public URL.

    The template shows previous outstanding + current bill = total payable
    (standard Indian wholesale bill format that builds trust).

    If ``upi_vpa`` is set, the invoice shows the UPI ID. Otherwise it shows
    "Contact for payment details".

    Returns:
        A publicly-accessible URL to the PDF in Supabase Storage.
    """
    total_payable = amount + previous_outstanding

    template = _jinja_env.get_template("invoice.html")
    html_str = template.render(
        business_name=business_name,
        business_phone=business_phone,
        business_address=business_address,
        client_name=client_name,
        client_address=client_address,
        invoice_number=invoice_number,
        invoice_date=_format_date(invoice_date),
        due_date=_format_date(due_date),
        credit_days=credit_days,
        amount=inr(amount),
        previous_outstanding=float(previous_outstanding),
        total_payable=inr(total_payable),
        upi_vpa=upi_vpa,
        description=description,
    )

    # WeasyPrint HTML → PDF (synchronous — runs in thread if needed).
    # Imported lazily: WeasyPrint dlopens GTK/Pango/Cairo at import time,
    # which fails on a bare Windows box. Railway's buildpack ships them.
    try:
        from weasyprint import HTML
    except OSError as exc:
        raise RuntimeError(
            "WeasyPrint native libraries (GTK3/Pango/Cairo) are not installed "
            "on this machine — PDF generation unavailable. On Windows install "
            "the GTK3 runtime; on Railway this works out of the box."
        ) from exc
    pdf_bytes: bytes = HTML(string=html_str).write_pdf()

    # Upload to Supabase Storage
    _ensure_bucket()
    db = require_db()
    file_path = f"{bill_id or invoice_number}/{invoice_number}.pdf"

    try:
        db.storage.from_(BUCKET_NAME).upload(
            file_path,
            pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    except Exception as exc:
        log.error("PDF upload failed for %s: %s", invoice_number, exc)
        raise

    public_url = db.storage.from_(BUCKET_NAME).get_public_url(file_path)
    log.info("Invoice PDF uploaded: %s", public_url)
    return public_url
