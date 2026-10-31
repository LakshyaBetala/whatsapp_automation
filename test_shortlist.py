"""Start with ten: the ranking, the opt-in, and the fortnight review."""
import datetime as dt
import sys
import types
from decimal import Decimal

sys.modules.setdefault("weasyprint", types.ModuleType("weasyprint"))

from app.services import shortlist                                  # noqa: E402

TODAY = dt.date(2026, 11, 1)


def _c(cid, name, **kw):
    row = {"id": cid, "name": name, "whatsapp_number": "919000000001",
           "reminders_enabled": False, "excluded": False}
    row.update(kw)
    return row


# ── ranking ────────────────────────────────────────────────────────────────

def test_rank_puts_old_small_money_above_big_fresh_money():
    """A lakh three days late is business. Twenty thousand a year late is the
    customer the owner actually wants chased."""
    clients = [_c("fresh", "Big Fresh"), _c("stale", "Small Stale")]
    totals = {"fresh": Decimal("1000000"), "stale": Decimal("20000")}
    overdue = {"fresh": 2, "stale": 300}
    assert [r["id"] for r in shortlist.rank(clients, totals, overdue)] == ["stale", "fresh"]


def test_rank_skips_everyone_asva_cannot_or_should_not_chase():
    clients = [
        _c("on", "Already On", reminders_enabled=True),
        _c("ex", "Do Not Chase", excluded=True),
        _c("nonum", "No Number", whatsapp_number=""),
        _c("nomoney", "Paid Up"),
        _c("good", "Chase Me"),
    ]
    totals = {"on": Decimal("5000"), "ex": Decimal("5000"), "nonum": Decimal("5000"),
              "nomoney": Decimal("0"), "good": Decimal("5000")}
    overdue = {k: 40 for k in totals}
    assert [r["id"] for r in shortlist.rank(clients, totals, overdue)] == ["good"]


def test_rank_still_orders_a_shop_with_nothing_overdue():
    """late=0 must not collapse every score to zero and return an arbitrary
    order: with nothing past due, the biggest outstanding leads."""
    clients = [_c("a", "A"), _c("b", "B")]
    totals = {"a": Decimal("1000"), "b": Decimal("9000")}
    ranked = shortlist.rank(clients, totals, {})
    assert [r["id"] for r in ranked] == ["b", "a"]
    assert all(r["score"] > 0 for r in ranked)


# ── the three states ───────────────────────────────────────────────────────

def test_stage_asks_for_ten_when_nobody_is_being_chased():
    st = shortlist.stage([_c("a", "A"), _c("b", "B")], TODAY)
    assert st["what"] == "pick"
    assert st["on"] == 0


def test_stage_stays_quiet_during_the_first_fortnight():
    picked = _c("a", "A", reminders_enabled=True, reminder_anchor="2026-10-25")
    assert shortlist.stage([picked], TODAY)["what"] == "none"


def test_stage_asks_for_the_next_fifty_after_two_weeks():
    picked = [_c(f"c{i}", f"C{i}", reminders_enabled=True,
                 reminder_anchor="2026-10-18") for i in range(10)]
    st = shortlist.stage(picked, TODAY)
    assert st["what"] == "review"
    assert st["on"] == 10
    assert st["since"] == dt.date(2026, 10, 18)
    assert len(st["ids"]) == 10


def test_stage_stops_asking_once_the_shop_has_committed():
    """Past REVIEW_UNTIL the owner has clearly bought in; nagging him to widen
    further is exactly the behaviour that got 88 percent switched off."""
    picked = [_c(f"c{i}", f"C{i}", reminders_enabled=True,
                 reminder_anchor="2026-01-01") for i in range(shortlist.REVIEW_UNTIL)]
    assert shortlist.stage(picked, TODAY)["what"] == "none"


def test_stage_ignores_an_excluded_party_that_still_has_the_flag_set():
    odd = _c("a", "A", reminders_enabled=True, excluded=True,
             reminder_anchor="2026-01-01")
    assert shortlist.stage([odd], TODAY)["what"] == "pick"


def test_picked_on_survives_a_junk_anchor():
    rows = [_c("a", "A", reminders_enabled=True, reminder_anchor="not-a-date"),
            _c("b", "B", reminders_enabled=True, reminder_anchor="2026-10-10")]
    assert shortlist.picked_on(rows) == dt.date(2026, 10, 10)


# ── turning them on ────────────────────────────────────────────────────────

class _Tbl:
    def __init__(self, log):
        self.log = log
        self.patch = None
        self.filters = {}

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = list(vals)
        return self

    def execute(self):
        self.log.append((self.patch, dict(self.filters)))
        return types.SimpleNamespace(data=[])


class _DB:
    def __init__(self):
        self.calls = []

    def table(self, name):
        assert name == "clients"
        return _Tbl(self.calls)


def test_enable_scopes_by_business_and_anchors_today():
    """No RLS: an update without business_id could reach another shop's row."""
    db = _DB()
    assert shortlist.enable(db, "biz1", ["a", "b"]) == 2
    patch, filters = db.calls[0]
    assert patch["reminders_enabled"] is True
    assert patch["reminder_anchor"] == shortlist._today().isoformat()
    assert filters["business_id"] == "biz1"
    assert filters["id"] == ["a", "b"]


def test_enable_chunks_past_the_supabase_row_limit():
    db = _DB()
    assert shortlist.enable(db, "biz1", [str(i) for i in range(250)]) == 250
    assert [len(f["id"]) for _, f in db.calls] == [100, 100, 50]


def test_enable_does_nothing_with_an_empty_pick():
    db = _DB()
    assert shortlist.enable(db, "biz1", []) == 0
    assert db.calls == []
