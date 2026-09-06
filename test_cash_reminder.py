"""Batch collection method decides what the reminder carries:
  - UPI  -> a TAPPABLE https pay link (and a QR image). WhatsApp does not
             linkify "upi://", so the raw deep link arrived as dead grey text.
  - Bank -> NEFT/RTGS bank details + a cheque line, then UPI too
  - Bank -> A/c + IFSC + cheque in the message, no link and no QR
  - Cash -> NOTHING digital: no UPI link, no QR, no bank details (no paper trail
            for a cash / off-books customer). This is the key guarantee.
"""
import asyncio
import sys
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot, batches


def _send(monkeypatch, batch_upi):
    cap = {}

    async def fake_send(**kwargs):
        cap.update(kwargs)
        return {"sent": True}

    monkeypatch.setattr(bot.whatsapp, "send_template", fake_send)
    inv = (date.today() - timedelta(days=40)).isoformat()
    biz = {"id": "biz1", "business_name": "ACME TRADERS", "plan": "pro",
           "upi_vpa": "acme@ybl", "bank_account_no": "12345678901",
           "bank_ifsc": "HDFC0001234", "bank_account_name": "Acme Traders",
           "bank_name": "HDFC Bank", "msg_language": "english",
           "reminder_batches": [{"name": "B", "lang": "english", "upi": batch_upi,
                                 "hour": 11, "disc": 0}]}
    client = {"id": "c1", "name": "Ramesh", "whatsapp_number": "919812345678",
              "language": "hi", "reminder_batch": 0}
    entry = {"client": client, "total": Decimal("18400"),
             "bills": [{"id": "b1", "invoice_number": "INV1", "outstanding": 18400,
                        "invoice_date": inv, "due_date": inv}]}
    asyncio.run(bot._send_consolidated_reminder(biz, entry))
    return cap


def test_cash_batch_carries_no_digital_rails(monkeypatch):
    cap = _send(monkeypatch, batches.CASH_SENTINEL)
    txt = cap["message_text"]
    assert "cash" in txt.lower()
    assert "upi://" not in txt
    assert "IFSC" not in txt and "A/c" not in txt
    assert cap.get("image_base64") is None      # no QR attached for cash


def test_upi_batch_carries_a_tappable_pay_link(monkeypatch):
    cap = _send(monkeypatch, "")                 # blank = shop default UPI
    txt = cap["message_text"]
    # Must be an https link WhatsApp will actually make tappable, never upi://.
    assert "/pay/" in txt and "upi://" not in txt
    assert cap.get("image_base64")               # the QR still rides along
    assert "IFSC" not in txt                     # UPI batch stays UPI-only


def test_bank_batch_carries_bank_and_cheque(monkeypatch):
    cap = _send(monkeypatch, batches.BANK_SENTINEL)
    txt = cap["message_text"]
    assert "IFSC" in txt and "Cheque" in txt
    assert "A/c" in txt


def test_bank_batch_sends_no_link_and_no_qr(monkeypatch):
    # The account number and IFSC ARE the instruction: the payer starts the
    # NEFT/RTGS/IMPS from their own bank app. A pay link or a UPI QR on a bank
    # message only invites the wrong rail.
    cap = _send(monkeypatch, batches.BANK_SENTINEL)
    txt = cap["message_text"]
    assert "/pay/" not in txt and "upi://" not in txt
    assert cap.get("image_base64") is None
