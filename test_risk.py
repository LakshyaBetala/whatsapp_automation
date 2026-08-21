"""Credit-risk watch: pull-only, party-page-only, and QUIET by default. The whole
point is no alert fatigue - an ordinary party returns no advisory."""
from app.services import risk
from app.services import scorecard as sc


def test_quiet_for_ordinary_parties():
    # Owes money but not badly overdue, decent grade -> NO advisory (stays silent).
    assert risk.assess(50000, 0, sc.GRADE_RELIABLE)["advisory"] == ""
    assert risk.assess(50000, 20, sc.GRADE_RELIABLE)["advisory"] == ""
    assert risk.assess(50000, 44, sc.GRADE_NEW)["advisory"] == ""


def test_nothing_owed_is_silent():
    assert risk.assess(0, 200, sc.GRADE_RISKY)["level"] == risk.LOW
    assert risk.assess(0, 200, sc.GRADE_RISKY)["advisory"] == ""


def test_watch_when_over_45_days():
    r = risk.assess(80000, 50, sc.GRADE_RELIABLE)
    assert r["level"] == risk.WATCH and r["advisory"]


def test_high_when_over_90_days():
    r = risk.assess(240000, 95, sc.GRADE_RELIABLE)
    assert r["level"] == risk.HIGH and "High exposure" in r["advisory"]


def test_slow_payer_lowers_the_bar():
    # A slow payer trips WATCH earlier (>=30) and HIGH earlier (>=60).
    assert risk.assess(10000, 35, sc.GRADE_RISKY)["level"] == risk.WATCH
    assert risk.assess(10000, 65, sc.GRADE_RISKY)["level"] == risk.HIGH
    # ...but a reliable payer at the same lateness stays one notch calmer.
    assert risk.assess(10000, 35, sc.GRADE_RELIABLE)["level"] == risk.LOW
