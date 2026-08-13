"""Dormancy guard + customer re-opt-in.

Dormancy: a shop whose ASVA app hasn't checked in for `dormant_pause_days` gets
NO sends - not reminders, not the EOD digest, not the morning checkpoint - until
the owner opens ASVA again (last_seen refreshes). Fail-safe: unknown/never-seen
counts as dormant (don't send).

Re-opt-in: a customer who STOPped can resume themselves with START/CHALU; a stray
'start' from a not-stopped customer stays silent (no owner nag).
"""
import asyncio
import datetime as dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import license as lic
from app.services import bot

NOW = dt.datetime.now(dt.timezone.utc)


def _iso(days_ago):
    return (NOW - dt.timedelta(days=days_ago)).isoformat()


# ── is_dormant ────────────────────────────────────────────────────────────────
def test_never_seen_is_dormant():
    assert lic.is_dormant({"last_seen": None}) is True
    assert lic.is_dormant({}) is True


def test_fresh_is_not_dormant():
    assert lic.is_dormant({"last_seen": _iso(0)}) is False


def test_within_window_not_dormant():
    assert lic.is_dormant({"last_seen": _iso(3)}) is False   # < 4 days


def test_past_window_is_dormant():
    assert lic.is_dormant({"last_seen": _iso(5)}) is True    # > 4 days


def test_boundary_just_over():
    # 4 days + 1 minute -> dormant; well under -> not.
    over = (NOW - dt.timedelta(days=4, minutes=1)).isoformat()
    under = (NOW - dt.timedelta(days=3, hours=23)).isoformat()
    assert lic.is_dormant({"last_seen": over}) is True
    assert lic.is_dormant({"last_seen": under}) is False


def test_days_zero_disables_guard(monkeypatch):
    # 0 days -> guard off, never dormant (even if never seen).
    assert lic.is_dormant({"last_seen": None}, days=0) is False
    assert lic.is_dormant({"last_seen": _iso(99)}, days=0) is False


def test_bad_timestamp_is_dormant():
    assert lic.is_dormant({"last_seen": "not-a-date"}) is True


def test_custom_days():
    assert lic.is_dormant({"last_seen": _iso(2)}, days=1) is True
    assert lic.is_dormant({"last_seen": _iso(2)}, days=7) is False


# ── sweep skips a dormant shop ────────────────────────────────────────────────
class _R:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, name, store):
        self.name = name; self.store = store; self.f = []; self.ins = None
    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, f, v): self.f.append((f, v)); return self
    def neq(self, f, v): return self
    def in_(self, f, v): self.f.append((f, list(v))); return self
    def gte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def insert(self, row): self.ins = row; return self
    def update(self, row): self.ins = dict(row, _u=1); return self
    def upsert(self, row, **k): self.ins = row; return self
    def execute(self):
        if self.ins is not None:
            self.store.setdefault(self.name, []).append(self.ins)
            return _R([self.ins])
        return _R(list(self.store.get(self.name, [])))


class FakeDB:
    def __init__(self, tables): self.store = {k: list(v) for k, v in tables.items()}
    def table(self, name): return _Q(name, self.store)


def _biz(last_seen):
    today = dt.date.today()
    return {"id": "biz1", "business_name": "T", "whatsapp_number": "9", "plan": "pro",
            "blackout_dates": [], "reminders_enabled": True, "upi_vpa": "t@ok",
            "reminder_cadence": None, "weekly_off_day": None, "reminder_style": None,
            "reminder_custom_line": None, "reminder_hour": 0, "msg_language": "hinglish",
            "discount_pct": 0, "overdue_repeat_days": 7, "overdue_max_repeats": 3,
            "plan_expires_on": None, "reminder_batches": None, "catchup_date": None,
            "catchup_action": None, "created_at": (today - dt.timedelta(days=40)).isoformat(),
            "last_seen": last_seen}


def test_sweep_skips_dormant_business(monkeypatch):
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc
    today = dt.date.today()
    inv = (today - dt.timedelta(days=40)).isoformat()
    client = {"id": "c1", "name": "P", "whatsapp_number": "919812345678", "language": "hi",
              "reminders_enabled": True, "credit_days": 30, "reminder_batch": None,
              "reminder_anchor": None, "excluded": False, "created_at": inv}
    bills = [{"id": "b1", "invoice_number": "S-1", "amount": 5000.0, "outstanding": 5000.0,
              "status": "overdue", "due_date": (today - dt.timedelta(days=10)).isoformat(),
              "invoice_date": inv, "business_id": "biz1", "client_id": "c1", "clients": client}]

    async def fake_send(b, entry):
        raise AssertionError("dormant shop must NOT send")

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", fake_send)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)

    # Dormant (last seen 6 days ago) -> the sweep must send nothing and not raise.
    fake = FakeDB({"businesses": [_biz(_iso(6))], "bills": bills, "messages": [],
                   "sweep_runs": [{"run_date": today.isoformat(), "run_hour": 0}]})
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    asyncio.run(rs.run())   # no AssertionError == no send
    markers = [m for m in fake.store.get("messages", []) if m.get("template_name") == "cadence_marker"]
    assert markers == []


# ── customer re-opt-in ────────────────────────────────────────────────────────
class _OptinDB:
    def __init__(self, enabled):
        self.enabled = enabled; self.updates = []
    def table(self, name):
        return self
    # select path
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def update(self, patch): self._op = "update"; self.updates.append(patch); return self
    def execute(self):
        if getattr(self, "_op", None) == "select":
            return _R([{"reminders_enabled": self.enabled}])
        return _R([{"id": "c1"}])


def _run_optin(monkeypatch, enabled):
    db = _OptinDB(enabled)
    monkeypatch.setattr(bot, "require_db", lambda: db)
    owner = []
    async def fake_notify(bid, text): owner.append(text)
    monkeypatch.setattr(bot.whatsapp, "notify_owner", fake_notify)
    monkeypatch.setattr(bot, "_biz_is_en", lambda bid: True)
    client = {"id": "c1", "name": "Ramesh", "business_id": "b1"}
    out = asyncio.run(bot._handle_customer_optin(client, "919812345678"))
    return out, db.updates, owner


def test_optin_resumes_when_opted_out(monkeypatch):
    out, updates, owner = _run_optin(monkeypatch, enabled=False)
    assert updates == [{"reminders_enabled": True}]     # turned back on
    assert owner and "resume" in owner[0].lower()
    assert "reminders again" in out.lower()


def test_optin_silent_when_already_on(monkeypatch):
    out, updates, owner = _run_optin(monkeypatch, enabled=True)
    assert updates == []        # nothing changed
    assert owner == []          # owner NOT nagged
    assert out == ""            # stays silent
