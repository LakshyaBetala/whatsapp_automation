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
