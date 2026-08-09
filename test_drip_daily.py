"""Daily drip cap must be a DAILY budget, not a per-sweep-run budget.

The sweep runs hourly. Before the fix, the cap counter (sent_per_biz) reset on
every run, and already-sent parties dropped out of contention via _already_sent,
so each hourly run sent the NEXT `cap` parties - draining a 200-party backlog in
a few hours instead of `cap`/day over several days. This proves the budget now
carries across runs within the same day (seeded from today's cadence markers).

Uses a filter-aware fake DB (the shared one in test_sweep_consolidated ignores
filters, which can't express per-party dedup across two runs).
"""
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

IST = timezone(timedelta(hours=5, minutes=30))


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """A query that actually honours eq/neq/in_/gte filters, so message dedup
    (per bill+day) and the daily-seed count behave like PostgREST."""

    def __init__(self, name, store):
        self._name = name
        self._store = store          # dict: table -> list[row]
        self._filters = []           # (op, field, value)
        self._insert = None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def eq(self, f, v): self._filters.append(("eq", f, v)); return self
    def neq(self, f, v): self._filters.append(("neq", f, v)); return self
    def in_(self, f, v): self._filters.append(("in", f, v)); return self
    def gte(self, f, v): self._filters.append(("gte", f, v)); return self
    def lt(self, f, v): self._filters.append(("lt", f, v)); return self

    def insert(self, row): self._insert = row; return self

    def upsert(self, row, **k): self._insert = row; return self

    def update(self, row): self._insert = dict(row, _update=True); return self

    def _match(self, row):
        for op, f, v in self._filters:
            cell = row.get(f)
            if op == "eq" and cell != v:
                return False
            if op == "neq" and cell == v:
                return False
            if op == "in" and cell not in v:
                return False
            if op == "gte" and not (cell is not None and str(cell) >= str(v)):
                return False
            if op == "lt" and not (cell is not None and str(cell) < str(v)):
                return False
        return True

    def execute(self):
        if self._insert is not None:
            saved = dict(self._insert)
            saved.pop("_update", None)
            # Mirror the Postgres default: rows get a created_at at insert time.
            saved.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            self._store.setdefault(self._name, []).append(saved)
            return _Result([saved])
        rows = [r for r in self._store.get(self._name, []) if self._match(r)]
        return _Result(rows)


class FakeDB:
    def __init__(self, tables):
        self._store = {k: list(v) for k, v in tables.items()}

    def table(self, name):
        return _Query(name, self._store)

    def rows(self, name):
        return self._store.get(name, [])


def _make(n_parties, plan="pro"):
    today = date.today()
    inv = (today - timedelta(days=40)).isoformat()
    due = (today - timedelta(days=10)).isoformat()   # day-37 overdue point due
    bills = []
    for i in range(n_parties):
        cid = f"c{i}"
        client = {"id": cid, "name": f"Party {i}", "whatsapp_number": f"9198000000{i:02d}",
                  "language": "hi", "reminders_enabled": True, "credit_days": 30,
                  "reminder_batch": None, "reminder_anchor": None,
                  "excluded": False, "created_at": inv}
        # Distinct outstanding so priority order is deterministic.
        bills.append({"id": f"b{i}", "invoice_number": f"S-{i}", "amount": 1000.0 + i,
                      "outstanding": 1000.0 + i, "status": "overdue", "due_date": due,
                      "invoice_date": inv, "business_id": "biz1", "client_id": cid,
                      "clients": client})
    biz = {"id": "biz1", "business_name": "TEST CO", "whatsapp_number": "919444294894",
           "plan": plan, "blackout_dates": [], "reminders_enabled": True,
           "upi_vpa": "t@ok", "reminder_cadence": None, "weekly_off_day": None,
           "reminder_style": None, "reminder_custom_line": None, "reminder_hour": 0,
           "msg_language": "hinglish", "discount_pct": 0, "overdue_repeat_days": 7,
           "overdue_max_repeats": 3, "plan_expires_on": None, "reminder_batches": None,
           "catchup_date": None, "catchup_action": None, "created_at": inv}
    return biz, bills


def _run_once(monkeypatch, fake, cap):
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)
    # Force a small, predictable cap regardless of plan/warm-up.
    monkeypatch.setattr(rs, "_daily_cap", lambda biz, today: cap)
    sends = []

    async def fake_consolidated(b, entry):
        sends.append(entry["client"]["id"])
        return True, "sent"

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", fake_consolidated)
    asyncio.run(rs.run())
    return sends


def test_daily_cap_carries_across_hourly_runs(monkeypatch):
    biz, bills = _make(5)
    fake = FakeDB({"businesses": [biz], "bills": bills, "messages": [],
                   "sweep_runs": [{"run_date": date.today().isoformat(), "run_hour": 0}]})

    # Hour 1: cap is 2 -> exactly 2 parties messaged.
    first = _run_once(monkeypatch, fake, cap=2)
    assert len(first) == 2, f"first run should send cap=2, sent {len(first)}"

    # Hour 2 (same day): the 2 already-sent parties are deduped out AND the daily
    # budget is already spent -> ZERO more go out. Before the fix this sent 2 more.
    second = _run_once(monkeypatch, fake, cap=2)
    assert second == [], f"same-day second run must send 0 more, sent {second}"

    # Only 2 distinct parties messaged all day.
    markers = [m for m in fake.rows("messages") if m.get("template_name") == "cadence_marker"]
    assert len({m["client_id"] for m in markers}) == 2


def test_priority_order_picks_biggest_oldest_first(monkeypatch):
    # 5 parties, cap 2 -> the two with the largest outstanding win the slots.
    biz, bills = _make(5)
    fake = FakeDB({"businesses": [biz], "bills": bills, "messages": [],
                   "sweep_runs": [{"run_date": date.today().isoformat(), "run_hour": 0}]})
    first = _run_once(monkeypatch, fake, cap=2)
    # Party 4 (1004) and Party 3 (1003) have the biggest outstanding.
    assert set(first) == {"c4", "c3"}, f"expected biggest-first, got {first}"
