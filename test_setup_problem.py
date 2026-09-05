"""A shop that is online but importing nothing must never look healthy.

The MANJUSHA case: Tally answered, vouchers flowed, but the debtor group walk
returned nothing, so the shop sat at "0 parties / All clear" for days while
every sync reported success.
"""
from app.routers import tally as tally_router


class _FakeTable:
    def __init__(self, sink): self.sink = sink; self._payload = None
    def update(self, payload): self._payload = payload; return self
    def eq(self, _col, _val): return self
    def execute(self): self.sink.append(self._payload); return self


class _FakeDB:
    def __init__(self): self.writes = []
    def table(self, _name): return _FakeTable(self.writes)


class _BoomDB:
    def table(self, _name): raise RuntimeError("supabase down")


def test_flagging_records_the_problem_and_a_timestamp():
    db = _FakeDB()
    tally_router._flag_setup_problem(db, "biz-1", "no customer group found")
    assert len(db.writes) == 1
    w = db.writes[0]
    assert w["setup_problem"] == "no customer group found"
    assert w["setup_problem_at"]          # stamped so we know how long it has been broken


def test_clearing_resets_both_columns():
    db = _FakeDB()
    tally_router._clear_setup_problem(db, "biz-1")
    assert db.writes == [{"setup_problem": None, "setup_problem_at": None}]


def test_long_problem_text_is_truncated_to_the_column():
    db = _FakeDB()
    tally_router._flag_setup_problem(db, "biz-1", "x" * 900)
    assert len(db.writes[0]["setup_problem"]) == 400


def test_flagging_never_breaks_a_sync():
    # Diagnostics are best-effort: a DB hiccup must not fail the shop's sync.
    tally_router._flag_setup_problem(_BoomDB(), "biz-1", "problem")
    tally_router._clear_setup_problem(_BoomDB(), "biz-1")
