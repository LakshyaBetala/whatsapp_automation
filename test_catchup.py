"""Missed-hour catch-up confirmation (migration 022).

If the batch's send hour passed with NO sweep run (ASVA/laptop was off),
late reminders are HELD until the owner decides:
  - undecided -> nothing sends, ONE owner alert (catchup_notice)
  - 'send'    -> reminders go out on the next sweep
  - 'skip'    -> nothing today, cadence continues tomorrow (no stacking)
On-time sends (sweep running at the batch hour) are never gated, and a
sweep that DID run at the batch hour keeps same-day catch-up automatic.

Also covers /admin/skip-today: cancel ONE party's reminder for today.
"""
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from test_sweep_consolidated import FakeDB

from app.jobs.reminder_sweep import IST


def _fixed_now(monkeypatch, rs, hour: int):
    fixed = datetime.now(IST).replace(hour=hour, minute=5, second=0, microsecond=0)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(rs, "datetime", _DT)
    return fixed


def _fixture(today, *, reminder_hour=11, catchup_date=None, catchup_action=None,
             sweep_runs=None):
    client = {"id": "c1", "name": "Late Traders", "whatsapp_number": "919812345678",
              "language": "hi", "reminders_enabled": True, "credit_days": 30,
              "reminder_batch": None, "reminder_anchor": None,
              "created_at": (today - timedelta(days=40)).isoformat()}
    bills = [{"id": "b1", "invoice_number": "S-1", "amount": 9000.0,
              "outstanding": 9000.0, "status": "overdue",
              "due_date": (today - timedelta(days=10)).isoformat(),
              "invoice_date": (today - timedelta(days=40)).isoformat(),
              "business_id": "biz1", "client_id": "c1", "clients": client}]
    biz = {"id": "biz1", "business_name": "TEST CO", "whatsapp_number": "919444294894",
           "plan": "pro", "blackout_dates": [], "reminders_enabled": True,
           "upi_vpa": "t@ok", "reminder_cadence": None, "weekly_off_day": None,
           "reminder_style": None, "reminder_custom_line": None,
           "reminder_hour": reminder_hour, "msg_language": "hinglish",
           "discount_pct": 0, "overdue_repeat_days": 7, "overdue_max_repeats": 3,
           "plan_expires_on": None, "reminder_batches": None,
           "catchup_date": catchup_date, "catchup_action": catchup_action}
    return FakeDB({"businesses": [biz], "bills": bills, "messages": [],
                   "sweep_runs": sweep_runs or []})


def _run(monkeypatch, fake, hour):
    from app.jobs import reminder_sweep as rs
    from app.services import bot as bot_svc

    _fixed_now(monkeypatch, rs, hour)
    monkeypatch.setattr(rs, "require_db", lambda: fake)
    monkeypatch.setattr(rs.settings, "send_gap_min_s", 0.0)
    monkeypatch.setattr(rs.settings, "send_gap_max_s", 0.0)

    sends, alerts = [], []

    async def fake_consolidated(b, entry):
        sends.append(entry)
        return True, "sent"

    async def fake_template(**kw):
        alerts.append(kw)
        return {"sent": True}

    monkeypatch.setattr(bot_svc, "_send_consolidated_reminder", fake_consolidated)
    monkeypatch.setattr(rs.whatsapp, "send_template", fake_template)

    asyncio.run(rs.run())
    return sends, alerts


def test_missed_hour_holds_sends_and_alerts_owner_once(monkeypatch):
    """Batch hour 11, ASVA off at 11, boots at 15 -> nothing sends; the owner
    gets ONE catchup_notice; no cadence markers are written."""
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[])   # no run at 11
    sends, alerts = _run(monkeypatch, fake, hour=15)

    assert sends == []
    notices = [a for a in alerts if a.get("campaign_name") == "catchup_notice"]
    assert len(notices) == 1
    assert notices[0]["to_number"] == "919444294894"   # the OWNER, not the party
    markers = [m for m in fake.writes["messages"] if m.get("template_name") == "cadence_marker"]
    assert markers == []


def test_owner_pressed_send_releases_the_reminders(monkeypatch):
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[],
                    catchup_date=today.isoformat(), catchup_action="send")
    sends, alerts = _run(monkeypatch, fake, hour=15)

    assert len(sends) == 1
    assert float(sends[0]["total"]) == 9000.0
    assert [a for a in alerts if a.get("campaign_name") == "catchup_notice"] == []


def test_owner_pressed_skip_drops_today_silently(monkeypatch):
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[],
                    catchup_date=today.isoformat(), catchup_action="skip")
    sends, alerts = _run(monkeypatch, fake, hour=15)

    assert sends == []
    assert [a for a in alerts if a.get("campaign_name") == "catchup_notice"] == []
    markers = [m for m in fake.writes["messages"] if m.get("template_name") == "cadence_marker"]
    assert markers == []   # nothing marked -> tomorrow's cadence is untouched


def test_on_time_send_is_never_gated(monkeypatch):
    """Sweep running AT the batch hour sends normally - no confirmation."""
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[])
    sends, alerts = _run(monkeypatch, fake, hour=11)

    assert len(sends) == 1
    assert [a for a in alerts if a.get("campaign_name") == "catchup_notice"] == []


def test_ran_at_batch_hour_keeps_same_day_catchup_automatic(monkeypatch):
    """ASVA WAS on at 11 (heartbeat exists). A party becoming due later the
    same day (owner enabled at 15:00) still sends automatically."""
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[{"run_hour": 11}])
    sends, alerts = _run(monkeypatch, fake, hour=15)

    assert len(sends) == 1
    assert [a for a in alerts if a.get("campaign_name") == "catchup_notice"] == []


def test_holiday_pauses_everything_even_with_decision_send(monkeypatch):
    """A calendar-marked holiday beats everything: no sends, no owner alert,
    no markers - the reminder moves to the next working day by itself."""
    today = date.today()
    fake = _fixture(today, reminder_hour=11, sweep_runs=[],
                    catchup_date=today.isoformat(), catchup_action="send")
    fake.tables["businesses"][0]["blackout_dates"] = [today.isoformat()]
    sends, alerts = _run(monkeypatch, fake, hour=15)

    assert sends == []
    assert alerts == []
    markers = [m for m in fake.writes.get("messages", [])
               if m.get("template_name") == "cadence_marker"]
    assert markers == []


def test_skip_today_marks_points_skipped(monkeypatch):
    """/admin/skip-today cancels TODAY's reminder for one party by marking the
    reached cadence point 'skipped' (dedup counts it; sweep won't send)."""
    from app.routers import admin as adm

    today = date.today()
    client = {"id": "c1", "name": "P", "credit_days": 30,
              "reminder_anchor": None,
              "created_at": (today - timedelta(days=40)).isoformat()}
    bills = [{"id": "b1",
              "invoice_date": (today - timedelta(days=40)).isoformat(),
              "due_date": (today - timedelta(days=10)).isoformat()}]
    biz = {"id": "biz1", "reminder_cadence": None, "overdue_repeat_days": 7,
           "overdue_max_repeats": 3}
    fake = FakeDB({"clients": [client], "bills": bills, "messages": []})
    monkeypatch.setattr(adm, "require_db", lambda: fake)
    monkeypatch.setattr(adm, "_biz_by_token", lambda token: biz)

    res = asyncio.run(adm.admin_skip_today(
        adm.SkipTodayPayload(token="t", client_id="c1")))

    assert res["ok"] is True
    assert res["skipped_points"] == 1
    rows = [m for m in fake.writes["messages"] if m.get("template_name") == "skipped_by_owner"]
    assert len(rows) == 1
    assert rows[0]["delivery_status"] == "skipped"
    assert rows[0]["reminder_day"] == 37   # day-30 due + 7-day overdue point
