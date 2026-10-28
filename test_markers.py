"""Cadence markers follow the real message they stand for."""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import markers


class _Q:
    def __init__(self, calls, name):
        self.calls, self.name, self.ops = calls, name, []
    def update(self, patch): self.ops.append(("update", patch)); return self
    def delete(self): self.ops.append(("delete",)); return self
    def eq(self, k, v): self.ops.append(("eq", k, v)); return self
    def execute(self):
        self.calls.append((self.name, self.ops))
        return type("R", (), {"data": []})()


class FakeDB:
    def __init__(self): self.calls = []
    def table(self, name): return _Q(self.calls, name)


def test_delivered_flips_markers_to_sent():
    db = FakeDB()
    markers.settle(db, "m1", delivered=True)
    (name, ops), = db.calls
    assert name == "messages"
    assert ("update", {"delivery_status": "sent"}) in ops
    assert ("eq", "parent_message_id", "m1") in ops
    assert ("eq", "template_name", "cadence_marker") in ops


def test_not_delivered_deletes_markers_so_the_point_retries():
    db = FakeDB()
    markers.settle(db, "m1", delivered=False)
    (name, ops), = db.calls
    assert ("delete",) in ops and ("eq", "parent_message_id", "m1") in ops


def test_no_parent_id_does_nothing():
    db = FakeDB()
    markers.settle(db, None, delivered=False)
    assert db.calls == []


def test_db_error_never_raises():
    class Boom:
        def table(self, name): raise RuntimeError("down")
    markers.settle(Boom(), "m1", delivered=True)   # must not raise


# ── wiring: every final outbox state settles its markers ──────────────────
class _Upd:
    """Fake for outbox._mark: update(...).eq(...).eq(...).execute() returns the row."""
    def __init__(self, row): self.row = row
    def update(self, patch): return self
    def eq(self, *a): return self
    def select(self, *a): return self
    def lt(self, *a): return self
    def limit(self, *a): return self
    def execute(self): return type("R", (), {"data": [self.row]})()


class OutboxDB:
    def __init__(self, row): self.row = row
    def table(self, name): return _Upd(self.row)


def _record(monkeypatch):
    seen = []
    monkeypatch.setattr(markers, "settle",
                        lambda db, mid, delivered, permanent=False:
                        seen.append((mid, delivered, permanent)))
    return seen


def test_laptop_ack_sent_settles_delivered(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    monkeypatch.setattr(outbox, "_cleanup_pdf", lambda db, mid: None)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "sent")
    assert seen == [("m1", True, False)]


def test_laptop_ack_failed_settles_not_delivered(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "failed")
    assert seen == [("m1", False, False)]   # transient: the marker goes, it retries


def test_a_number_not_on_whatsapp_keeps_its_marker(monkeypatch):
    """Deleting the marker means the next sweep chases the same dead number
    again, every hour, forever - nine parties did exactly that 120 times."""
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "failed",
               error="not_on_whatsapp")
    assert seen == [("m1", False, True)]


def test_an_unknown_error_is_still_treated_as_transient(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "failed",
               error="socket hung up")
    assert seen == [("m1", False, False)]


def test_laptop_ack_still_queued_settles_nothing(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "queued")
    assert seen == []


def test_held_reminder_releases_its_markers(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox._hold(OutboxDB({}), "b1", "o1", "m1")
    assert seen == [("m1", False, False)]


def test_server_sweep_mark_settles(monkeypatch):
    from app.jobs import outbox_sweep
    seen = _record(monkeypatch)
    monkeypatch.setattr(outbox_sweep, "_cleanup_pdf", lambda db, mid: None)
    db = OutboxDB({})
    outbox_sweep._mark(db, {"id": "o1", "message_db_id": "m1"}, "sent", 1, None)
    outbox_sweep._mark(db, {"id": "o2", "message_db_id": "m2"}, "failed", 3, "x")
    assert seen == [("m1", True, False), ("m2", False, False)]


def test_stale_expiry_releases_markers(monkeypatch):
    from app.jobs import outbox_sweep
    seen = _record(monkeypatch)
    row = {"id": "o1", "business_id": "b1", "message_db_id": "m1", "attempts": 0}
    monkeypatch.setattr("app.db.get_client", lambda: OutboxDB(row))
    asyncio.run(outbox_sweep.expire_stale())
    assert ("m1", False, False) in seen


# ── the sweep links markers to the real message ───────────────────────────
def test_marker_row_for_a_queued_message():
    from app.jobs.reminder_sweep import _marker_row
    row = _marker_row("b1", "c1", "bill1", 15, parent_id="m9", queued=True)
    assert row["template_name"] == "cadence_marker"
    assert row["delivery_status"] == "queued" and row["parent_message_id"] == "m9"
    assert row["bill_id"] == "bill1" and row["reminder_day"] == 15 and row["type"] == "reminder"


def test_marker_row_for_a_direct_send():
    from app.jobs.reminder_sweep import _marker_row
    row = _marker_row("b1", "c1", "bill1", 15, parent_id=None, queued=False)
    assert row["delivery_status"] == "sent" and "parent_message_id" not in row


# ── debtor cap: the plan covers the top-N parties by priority ─────────────
def test_covered_parties_takes_top_n_per_business():
    from app.jobs.reminder_sweep import _covered_parties
    ordered = [(("a", "c1"), {}), (("a", "c2"), {}), (("a", "c3"), {}),
               (("b", "d1"), {}), (("b", "d2"), {})]
    covered = _covered_parties(ordered, {"a": 2, "b": 5})
    assert covered == {("a", "c1"), ("a", "c2"), ("b", "d1"), ("b", "d2")}


def test_zero_cap_means_no_cap():
    from app.jobs.reminder_sweep import _covered_parties
    assert _covered_parties([(("a", "c1"), {}), (("a", "c2"), {})], {"a": 0}) == {("a", "c1"), ("a", "c2")}
