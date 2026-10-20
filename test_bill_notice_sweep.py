"""The bill-notice sender: one combined message per customer, through the
normal send path (limits, subscription gate, outbox), never twice."""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs import bill_notice_sweep as sweep


class DB:
    def __init__(self, tables):
        self.tables, self.updates = tables, []

    def table(self, name):
        db = self

        class Q:
            def __init__(self): self.patch = None
            def select(self, *a, **k): return self
            def eq(self, *a): return self
            def lte(self, *a): return self
            def in_(self, *a): return self
            def limit(self, *a): return self
            def update(self, patch): self.patch = patch; return self
            def execute(self):
                if self.patch is not None:
                    db.updates.append((name, self.patch)); return type("R", (), {"data": []})()
                return type("R", (), {"data": list(db.tables.get(name, []))})()
        return Q()


BIZ = {"id": "b1", "business_name": "Shanti Electricals", "plan": "pro", "bill_notices": True,
       "upi_vpa": "shanti@okhdfc", "reminder_batches": None, "msg_language": "english"}
CLIENT = {"id": "c1", "name": "Ramesh Traders", "whatsapp_number": "919876543210",
          "language": "hi", "reminder_batch": None}
BILLS = [{"id": "x1", "invoice_number": "INV-71", "invoice_date": "2026-10-21",
          "due_date": "2026-11-20", "amount": 12500}]


def _tables(**over):
    t = {"bill_notices": [{"id": "n1", "business_id": "b1", "client_id": "c1", "bill_id": "x1"}],
         "businesses": [BIZ], "clients": [CLIENT], "bills": BILLS}
    t.update(over)
    return t


def _wire(monkeypatch, result):
    sent = []
    async def fake_send(**kw):
        sent.append(kw); return result
    monkeypatch.setattr(sweep.whatsapp, "send_message", fake_send)
    monkeypatch.setattr(sweep, "_outstanding", lambda db, b, c: 48250)
    monkeypatch.setattr(sweep.upi, "pay_page_url", lambda *a, **k: "https://app.tryasva.com/pay/x")
    return sent


def test_sends_one_combined_message_and_marks_sent(monkeypatch):
    sent = _wire(monkeypatch, {"queued": True, "db_id": "m9"})
    db = DB(_tables())
    asyncio.run(sweep._send_group(db, "b1", "c1"))
    assert len(sent) == 1
    kw = sent[0]
    assert kw["to_number"] == "919876543210" and kw["priority"] is True
    assert "INV-71" in kw["message_text"] and "48,250" in kw["message_text"]
    assert "https://app.tryasva.com/pay/x" in kw["message_text"]
    assert kw["template_name"] == "bill_notice"
    table, patch = db.updates[-1]
    assert table == "bill_notices" and patch["status"] == "sent" and patch["message_db_id"] == "m9"


def test_notices_off_or_no_number_are_skipped_not_sent(monkeypatch):
    sent = _wire(monkeypatch, {"queued": True})
    db = DB(_tables(businesses=[dict(BIZ, bill_notices=False)]))
    asyncio.run(sweep._send_group(db, "b1", "c1"))
    db2 = DB(_tables(clients=[dict(CLIENT, whatsapp_number=None)]))
    asyncio.run(sweep._send_group(db2, "b1", "c1"))
    assert sent == []
    assert db.updates[-1][1]["status"] == "skipped" and db2.updates[-1][1]["status"] == "skipped"


def test_blocked_send_is_skipped_with_the_reason(monkeypatch):
    _wire(monkeypatch, {"sent": False, "reason": "subscription_suspended"})
    db = DB(_tables())
    asyncio.run(sweep._send_group(db, "b1", "c1"))
    assert db.updates[-1][1]["status"] == "skipped"
    assert db.updates[-1][1]["note"] == "subscription_suspended"


def test_transient_failure_retries_later(monkeypatch):
    _wire(monkeypatch, {"sent": False, "reason": "wa_service_down"})
    db = DB(_tables())
    asyncio.run(sweep._send_group(db, "b1", "c1"))
    assert "send_after" in db.updates[-1][1] and "status" not in db.updates[-1][1]
