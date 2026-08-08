"""Priority-drip + per-plan cap (safe sending). Pure-function tests."""
from datetime import date, timedelta

from app.jobs.reminder_sweep import _daily_cap, _party_priority


def _biz(plan="starter", created=None):
    return {"plan": plan, "created_at": created}


def test_daily_cap_per_plan():
    today = date(2026, 6, 1)
    old = "2020-01-01"                      # well past warm-up
    assert _daily_cap(_biz("starter", old), today) == 50
    assert _daily_cap(_biz("growth", old), today) == 100
    assert _daily_cap(_biz("pro", old), today) == 150


def test_daily_cap_warmup_ramps_new_number():
    today = date(2026, 6, 10)
    # day 0 of a fresh shop -> 20, ramps 20/day, capped at the plan number
    assert _daily_cap(_biz("growth", today.isoformat()), today) == 20            # age 0
    assert _daily_cap(_biz("growth", (today - timedelta(days=1)).isoformat()), today) == 40   # age 1
    assert _daily_cap(_biz("growth", (today - timedelta(days=2)).isoformat()), today) == 60   # age 2
    # starter caps at 50 even mid-ramp
    assert _daily_cap(_biz("starter", (today - timedelta(days=3)).isoformat()), today) == 50   # 20+60 -> capped 50


def test_priority_bigger_and_older_first():
    today = date(2026, 6, 1)
    small_new = [{"outstanding": 1000, "invoice_date": "2026-05-30"}]     # 1000 x 2
    big_old = [{"outstanding": 50000, "invoice_date": "2026-01-01"}]      # huge x ~150
    assert _party_priority(big_old, today) > _party_priority(small_new, today)


def test_priority_orders_a_backlog_deterministically():
    today = date(2026, 6, 1)
    parties = {
        ("b", "c1"): {"bills": [{"outstanding": 500, "invoice_date": "2026-05-31"}]},
        ("b", "c2"): {"bills": [{"outstanding": 90000, "invoice_date": "2026-01-01"}]},
        ("b", "c3"): {"bills": [{"outstanding": 20000, "invoice_date": "2026-03-01"}]},
    }
    ordered = sorted(parties.items(),
                     key=lambda kv: (kv[0][0], -_party_priority(kv[1]["bills"], today)))
    ids = [k[1] for k, _ in ordered]
    assert ids == ["c2", "c3", "c1"]        # biggest x oldest first, smallest last
