"""Batch collection method decides what the reminder carries:
  - UPI  -> a upi:// link (and a QR image)
  - Bank -> NEFT/RTGS bank details + a cheque line, then UPI too
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


def test_upi_batch_carries_a_upi_link(monkeypatch):
    cap = _send(monkeypatch, "")                 # blank = shop default UPI
    assert "upi://" in cap["message_text"]
    assert "IFSC" not in cap["message_text"]     # UPI batch stays UPI-only


def test_bank_batch_carries_bank_and_cheque(monkeypatch):
    cap = _send(monkeypatch, batches.BANK_SENTINEL)
    txt = cap["message_text"]
    assert "IFSC" in txt and "Cheque" in txt
    assert "upi://" in txt                        # bank batch still offers UPI too
