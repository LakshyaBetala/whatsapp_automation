"""Sweep consolidation: a party with several open bills gets ONE message
(all bills combined, like Send Now), and every triggered (bill, day) point
is marked so nothing fires twice."""
import asyncio
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink
        self._insert = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, row):
        self._insert = row
        return self

    def upsert(self, row, **k):
        self._insert = row
        return self

    def update(self, row):
        self._insert = dict(row, _update=True)
        return self

    def execute(self):
        if self._insert is not None:
            saved = dict(self._insert)
            self._sink.append(saved)
            return _Result([saved])
        return _Result(self._rows)


class FakeDB:
    def __init__(self, tables):
        self.tables = tables
        self.writes = {}

    def table(self, name):
        self.writes.setdefault(name, [])
        return _Query(self.tables.get(name, []), self.writes[name])


def test_sweep_sends_one_consolidated_message_per_party(monkeypatch):
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc

    today = date.today()
    inv = (today - timedelta(days=40)).isoformat()   # 40 days old, 30-day term
    due = (today - timedelta(days=10)).isoformat()   # 10 days overdue -> day-37 point due
    client = {"id": "c1", "name": "Three Bills Traders", "whatsapp_number": "919812345678",
              "language": "hi", "reminders_enabled": True, "credit_days": 30,
              "reminder_batch": None, "reminder_anchor": None,
              "created_at": (today - timedelta(days=40)).isoformat()}
    bills = [
        {"id": f"b{i}", "invoice_number": f"S-{i}", "amount": 7000.0,
         "outstanding": 7000.0, "status": "overdue", "due_date": due,
         "invoice_date": inv, "business_id": "biz1", "client_id": "c1",
         "clients": client}
        for i in (1, 2, 3)
    ]
    biz = {"id": "biz1", "business_name": "TEST CO", "whatsapp_number": "919444294894",
           "plan": "pro", "blackout_dates": [], "reminders_enabled": True,
           "upi_vpa": "t@ok", "reminder_cadence": None, "weekly_off_day": None,
           "reminder_style": None, "reminder_custom_line": None, "reminder_hour": 0,
           "msg_language": "hinglish", "discount_pct": 0, "overdue_repeat_days": 7,
           "overdue_max_repeats": 3, "plan_expires_on": None, "reminder_batches": None}

    # sweep_runs stamp for hour 0 = ASVA ran at the batch hour today, so the
    # late send stays AUTOMATIC (catch-up confirmation is off; see test_catchup).
    fake = FakeDB({"businesses": [biz], "bills": bills, "messages": [],
                   "sweep_runs": [{"run_hour": 0}]})
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)

    sends = []

    async def fake_consolidated(b, entry):
        sends.append(entry)
        return True, "sent"

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", fake_consolidated)

    asyncio.run(rs.run())

    # ONE message for the party, covering all 3 bills with the full total
    assert len(sends) == 1
    entry = sends[0]
    assert len(entry["bills"]) == 3
    assert float(entry["total"]) == 21000.0
    # and all 3 (bill, day) cadence points marked -> nothing re-fires
    markers = [m for m in fake.writes["messages"] if m.get("template_name") == "cadence_marker"]
    assert len(markers) == 3
    assert {m["bill_id"] for m in markers} == {"b1", "b2", "b3"}
    assert all(m["reminder_day"] == 37 for m in markers)


def test_sweep_failed_send_marks_nothing(monkeypatch):
    """If the consolidated send fails (WhatsApp down), NO markers are written
    so the whole party retries on the next hourly sweep."""
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc

    today = date.today()
    client = {"id": "c1", "name": "P", "whatsapp_number": "919812345678",
              "language": "hi", "reminders_enabled": True, "credit_days": 30,
              "reminder_batch": None, "reminder_anchor": None,
              "created_at": (today - timedelta(days=40)).isoformat()}
    bills = [{"id": "b1", "invoice_number": "S-1", "amount": 100.0,
              "outstanding": 100.0, "status": "overdue",
              "due_date": (today - timedelta(days=10)).isoformat(),
              "invoice_date": (today - timedelta(days=40)).isoformat(),
              "business_id": "biz1", "client_id": "c1", "clients": client}]
    biz = {"id": "biz1", "business_name": "TEST CO", "whatsapp_number": "9",
           "plan": "pro", "blackout_dates": [], "upi_vpa": "t@ok",
           "reminder_cadence": None, "weekly_off_day": None, "reminder_style": None,
           "reminder_custom_line": None, "reminder_hour": 0, "msg_language": "hinglish",
           "discount_pct": 0, "overdue_repeat_days": 7, "overdue_max_repeats": 3,
           "plan_expires_on": None, "reminder_batches": None, "reminders_enabled": True}

    fake = FakeDB({"businesses": [biz], "bills": bills, "messages": [],
                   "sweep_runs": [{"run_hour": 0}]})
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)

    async def failing(b, entry):
        return False, "WhatsApp service band hai"

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", failing)

    asyncio.run(rs.run())

    markers = [m for m in fake.writes["messages"] if m.get("template_name") == "cadence_marker"]
    assert markers == []    # nothing marked -> retried next hour
