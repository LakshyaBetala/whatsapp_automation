"""A reminder must reach a customer once, even when the retry path fires.

The owners' "sent twice" reports had one cause. Every sender treats
`httpx.ReadTimeout` as transient and re-queues the row - but a ReadTimeout
means the request ARRIVED at wa_service and we merely did not hear the answer
in time. WhatsApp had already taken the message. The retry delivered it again.
It showed up most around a reconnect, because that is when answers are slowest,
which is exactly when the owners noticed it.

The fix is an idempotency key that wa_service remembers, so a retry is a no-op
instead of a second delivery. These tests pin the Python half of that contract:
every send path must carry a key, and for a queued row the key must be the row
id, because that is the one value that survives every retry and re-pull.

The ledger itself lives in wa_service/index.js and has no test harness; that
gap is recorded in docs/MVP-READINESS-2026-10-25.md.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import outbox


class _Res:
    def __init__(self, data):
        self.data = data


class _Tbl:
    """Enough of the query builder for outbox.pull."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return _Tbl([])

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def execute(self):
        return _Res(self._rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        if name == "businesses":
            return _Tbl([{"wa_transport": "laptop"}])
        if name == "wa_outbox":
            return _Tbl(self.rows)
        return _Tbl([])


ROW = {
    "id": "row-abc-123",
    "payload": {"phone": "919000000000", "message": "Your bill is due."},
    "attempts": 0,
    "created_at": "2099-01-01T00:00:00+00:00",   # never stale
    "message_db_id": None,
    "priority": True,
}


@pytest.fixture(autouse=True)
def _no_window(monkeypatch):
    """Deliver regardless of the clock, so the test is not time-of-day flaky."""
    monkeypatch.setattr(outbox, "within_send_window", lambda: True)
    monkeypatch.setattr(outbox.promises, "held_now", lambda db, ids: {})


def test_a_pulled_row_carries_its_row_id_as_the_send_key():
    out = outbox.pull(_DB([dict(ROW)]), "b1")
    assert len(out) == 1
    assert out[0]["payload"]["client_msg_id"] == "row-abc-123"


def test_the_key_is_the_same_on_every_pull_of_the_same_row():
    """A lost ack means the row is pulled again. If the key changed, wa_service
    could not tell the retry from a new message and would send twice."""
    db = _DB([dict(ROW)])
    first = outbox.pull(db, "b1")[0]["payload"]["client_msg_id"]
    second = outbox.pull(db, "b1")[0]["payload"]["client_msg_id"]
    assert first == second == "row-abc-123"


def test_stamping_the_key_does_not_mutate_the_stored_payload():
    """The row in the database must stay as queued, or a later read would carry
    a key that no longer matches the row it came from."""
    row = dict(ROW)
    row["payload"] = {"phone": "919000000000", "message": "hi"}
    outbox.pull(_DB([row]), "b1")
    assert "client_msg_id" not in row["payload"]


def test_the_rest_of_the_payload_is_untouched():
    out = outbox.pull(_DB([dict(ROW)]), "b1")[0]["payload"]
    assert out["phone"] == "919000000000"
    assert out["message"] == "Your bill is due."


def test_the_in_process_sweep_stamps_the_row_id_too():
    """outbox_sweep reads wa_outbox directly rather than through pull(), so it
    needs its own stamp - it was the path that produced the duplicates."""
    import inspect

    from app.jobs import outbox_sweep
    src = inspect.getsource(outbox_sweep.run)
    assert 'send_payload["client_msg_id"] = r["id"]' in src, \
        "the sweep posts a payload with no idempotency key"
    assert "json=send_payload" in src, "the sweep posts the unstamped payload"


def test_the_direct_path_mints_a_key():
    """A send that never touches the queue still gets retried by its caller."""
    import inspect

    from app.services import whatsapp
    src = inspect.getsource(whatsapp.send_message)
    assert '"client_msg_id"' in src, "the direct send carries no idempotency key"


def test_read_timeout_is_still_retried():
    """The retry itself is correct and must stay - a genuine timeout before
    delivery has to be resent. It is only safe because of the key above, so if
    someone removes the retry this test should make them think twice."""
    import httpx

    from app.jobs.outbox_sweep import _is_transient
    assert _is_transient(httpx.ReadTimeout("timed out")) is True
    assert _is_transient(httpx.ConnectError("refused")) is True
