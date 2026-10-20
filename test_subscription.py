"""Unit tests for the subscription lifecycle (server-side license)."""
from datetime import date, timedelta

from app.services import subscription as subs
from app.services.subscription import effective_status, days_left, GRACE_DAYS

TODAY = date(2026, 7, 5)


def test_active_before_expiry():
    assert effective_status(TODAY + timedelta(days=10), TODAY) == "active"
    assert effective_status(TODAY, TODAY) == "active"  # expiry day itself


def test_grace_window_is_three_days():
    assert GRACE_DAYS == 3
    assert effective_status(TODAY - timedelta(days=1), TODAY) == "grace"
    assert effective_status(TODAY - timedelta(days=2), TODAY) == "grace"


def test_suspended_after_grace():
    assert effective_status(TODAY - timedelta(days=3), TODAY) == "suspended"
    assert effective_status(TODAY - timedelta(days=90), TODAY) == "suspended"


def test_no_expiry_means_active():
    assert effective_status(None, TODAY) == "active"


def test_string_dates_accepted():
    assert effective_status("2026-07-10", TODAY) == "active"
    assert effective_status("2026-06-25", TODAY) == "suspended"


def test_days_left():
    assert days_left(TODAY + timedelta(days=5), TODAY) == 5
    assert days_left(TODAY - timedelta(days=2), TODAY) == -2
    assert days_left(None, TODAY) is None


def test_renewal_payment_line_with_upi(monkeypatch):
    monkeypatch.setattr(subs.settings, "operator_upi_id", "laksh@okhdfc")
    monkeypatch.setattr(subs.settings, "operator_upi_name", "ASVA")
    monkeypatch.setattr(subs.settings, "product_team_number", "919344110272")
    monkeypatch.setattr(subs.settings, "public_base_url", "https://app.tryasva.com")
    line = subs.renewal_payment_line("pro")
    assert "1,499" in line                      # pro (Recover) price, formatted
    assert "laksh@okhdfc" in line               # UPI id as copyable text
    assert "https://app.tryasva.com/pay?plan=pro" in line  # tappable UPI pay link
    assert "https://wa.me/919344110272" in line  # a CLICKABLE https contact link
    assert "upi://" not in line                  # no dead non-clickable scheme in the message


# ── free pilot (everyone Pro + active until a date) ──────────────────────────
def test_free_pilot_active_within_window(monkeypatch):
    monkeypatch.setattr(subs.settings, "free_pilot_until", "2026-09-15")
    assert subs.free_pilot_active(date(2026, 7, 31)) is True
    assert subs.free_pilot_active(date(2026, 9, 15)) is True      # inclusive
    assert subs.free_pilot_active(date(2026, 9, 16)) is False


def test_free_pilot_off_when_blank(monkeypatch):
    monkeypatch.setattr(subs.settings, "free_pilot_until", "")
    assert subs.free_pilot_active(date(2026, 7, 31)) is False


def test_live_status_never_suspends_during_pilot(monkeypatch):
    monkeypatch.setattr(subs.settings, "free_pilot_until", "2026-09-15")
    # a long-expired business is still 'active' to the send path during the pilot
    assert subs.live_status("2026-01-01", TODAY) == "active"
    # ...but the pure status math is unchanged
    assert effective_status("2026-01-01", TODAY) == "suspended"


def test_live_status_enforces_normally_after_pilot(monkeypatch):
    monkeypatch.setattr(subs.settings, "free_pilot_until", "")
    assert subs.live_status(TODAY - timedelta(days=90), TODAY) == "suspended"


def test_renewal_payment_line_blank_without_config(monkeypatch):
    monkeypatch.setattr(subs.settings, "operator_upi_id", "")
    monkeypatch.setattr(subs.settings, "product_team_number", "")
    assert subs.renewal_payment_line("pro") == ""


def test_renewal_payment_line_team_only_is_clickable(monkeypatch):
    monkeypatch.setattr(subs.settings, "operator_upi_id", "")
    monkeypatch.setattr(subs.settings, "product_team_number", "919344110272")
    line = subs.renewal_payment_line("pro")
    assert "https://wa.me/919344110272" in line and "upi://" not in line


def _sc_setup(monkeypatch, biz, left, live="active", debtors=320):
    from app.jobs import subscription_check as sc
    sent = []
    async def fake_notify(bid, text): sent.append(text)
    monkeypatch.setattr(sc.whatsapp, "notify_owner", fake_notify)
    monkeypatch.setattr(sc.subs, "free_pilot_active", lambda: False)
    monkeypatch.setattr(sc.subs, "days_left", lambda exp, today=None: left)
    monkeypatch.setattr(sc.subs, "effective_status", lambda exp, today=None: live)
    monkeypatch.setattr(sc, "active_debtor_count", lambda db, bid: debtors)

    class Q:
        def select(self, *a): return self
        def update(self, *a): return self
        def eq(self, *a): return self
        def execute(self): return type("R", (), {"data": [biz]})()
    monkeypatch.setattr(sc, "require_db", lambda: type("DB", (), {"table": lambda s, n: Q()})())
    return sc, sent


def test_trial_notice_names_the_plan_by_debtors(monkeypatch):
    import asyncio
    biz = {"id": "b1", "business_name": "Ram Traders", "plan": "pro",
           "subscription_status": "trial", "plan_expires_on": "2026-10-22"}
    sc, sent = _sc_setup(monkeypatch, biz, left=2)
    asyncio.run(sc.run())
    assert sent and "trial" in sent[0] and "Collect" in sent[0]
    assert "999" in sent[0] and "320" in sent[0]
    assert "—" not in sent[0] and "–" not in sent[0]


def test_paid_renew_notice_at_7_3_1_days(monkeypatch):
    import asyncio
    biz = {"id": "b1", "business_name": "Ram Traders", "plan": "growth",
           "subscription_status": "active", "plan_expires_on": "2026-10-27"}
    for left, expect in ((7, True), (3, True), (1, True), (5, False)):
        sc, sent = _sc_setup(monkeypatch, biz, left=left)
        asyncio.run(sc.run())
        assert bool(sent) is expect, left
