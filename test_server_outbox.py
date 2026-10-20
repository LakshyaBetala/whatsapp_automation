"""The server delivers the queue of shops whose WhatsApp runs on ASVA's
server, exactly as a laptop would (same pull, same ack, same gaps), plus an
hourly cap per number for ban safety."""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.jobs import server_outbox as so


class DB:
    def __init__(self): self.updates = []
    def table(self, name):
        db = self
        class Q:
            def update(self, patch): db.updates.append(patch); return self
            def eq(self, *a): return self
            def execute(self): return type("R", (), {"data": []})()
        return Q()


def _wire(monkeypatch, *, ready=True, items=(), results=(), sent_last_hour=0):
    rec = {"acks": [], "connected": [], "pulled": [], "sent": []}
    async def fake_status(port): return {"ready": ready, "qr": None}
    monkeypatch.setattr(so.wa_server, "status", fake_status)
    monkeypatch.setattr(so.wa_server, "mark_connected",
                        lambda db, bid, already_linked: rec["connected"].append((bid, already_linked)))
    def fake_pull(db, bid, limit=15, caller="laptop"):
        rec["pulled"].append((bid, limit, caller)); return list(items)
    monkeypatch.setattr(so.outbox, "pull", fake_pull)
    monkeypatch.setattr(so.outbox, "ack", lambda db, bid, rid, st, attempts=1, error=None:
                        rec["acks"].append((rid, st, error)) or True)
    res = list(results)
    async def fake_send(port, payload):
        rec["sent"].append(payload); return res.pop(0)
    monkeypatch.setattr(so, "_send", fake_send)
    monkeypatch.setattr(so, "_sent_last_hour", lambda db, bid: sent_last_hour)
    async def no_sleep(*a): return None
    monkeypatch.setattr(so.asyncio, "sleep", no_sleep)
    return rec


SHOP = {"id": "b1", "wa_port": 4101, "wa_transport": "server", "wa_linked_at": "x",
        "wa_server_requested_at": "x"}


def test_not_ready_sends_nothing_and_records_it(monkeypatch):
    rec = _wire(monkeypatch, ready=False, items=[{"id": "o1", "payload": {}, "attempts": 0}])
    db = DB()
    asyncio.run(so._drain(db, dict(SHOP)))
    assert rec["pulled"] == [] and rec["sent"] == []
    assert any(u.get("wa_ready") is False for u in db.updates)


def test_first_ready_flips_the_shop_to_server_then_delivers(monkeypatch):
    rec = _wire(monkeypatch, items=[{"id": "o1", "payload": {"phone": "9"}, "attempts": 0}],
                results=[("sent", None)])
    shop = dict(SHOP, wa_transport="laptop", wa_linked_at=None)
    asyncio.run(so._drain(DB(), shop))
    assert rec["connected"] == [("b1", False)]
    assert rec["pulled"] == [("b1", 15, "server")]
    assert rec["acks"] == [("o1", "sent", None)]


def test_transient_keeps_the_queue_and_stops(monkeypatch):
    items = [{"id": "o1", "payload": {}, "attempts": 0}, {"id": "o2", "payload": {}, "attempts": 0}]
    rec = _wire(monkeypatch, items=items, results=[("transient", "WhatsApp not ready")])
    asyncio.run(so._drain(DB(), dict(SHOP)))
    assert rec["acks"] == [("o1", "queued", "WhatsApp not ready")]   # o2 untouched


def test_not_on_whatsapp_fails_at_once(monkeypatch):
    rec = _wire(monkeypatch, items=[{"id": "o1", "payload": {}, "attempts": 0}],
                results=[("failed", "not_on_whatsapp")])
    asyncio.run(so._drain(DB(), dict(SHOP)))
    assert rec["acks"] == [("o1", "failed", "not_on_whatsapp")]


def test_hourly_cap_limits_the_pull(monkeypatch):
    rec = _wire(monkeypatch, sent_last_hour=so.settings.wa_server_hourly_cap)
    asyncio.run(so._drain(DB(), dict(SHOP)))
    assert rec["pulled"] == []
    rec = _wire(monkeypatch, sent_last_hour=so.settings.wa_server_hourly_cap - 4)
    asyncio.run(so._drain(DB(), dict(SHOP)))
    assert rec["pulled"] == [("b1", 4, "server")]
