"""Escalation ladder: tone firms up with days overdue; promise breaks bump it;
the formal letter is firm but never a legal threat."""
from app.services import escalation as esc


def test_tier_boundaries():
    assert esc.tier_for(0) == esc.GENTLE
    assert esc.tier_for(7) == esc.GENTLE
    assert esc.tier_for(8) == esc.STANDARD
    assert esc.tier_for(21) == esc.STANDARD
    assert esc.tier_for(22) == esc.FIRM
    assert esc.tier_for(45) == esc.FIRM
    assert esc.tier_for(46) == esc.FINAL
    assert esc.tier_for(400) == esc.FINAL
    assert esc.tier_for(None) == esc.GENTLE


def test_broken_promise_bumps_to_at_least_firm():
    assert esc.tier_for(2, promise_broken=True) == esc.FIRM      # gentle -> firm
    assert esc.tier_for(10, promise_broken=True) == esc.FIRM     # standard -> firm
    assert esc.tier_for(60, promise_broken=True) == esc.FINAL    # already final, stays


def test_intro_line_tone_rises():
    biz = "ACME"
    assert "reminder" in esc.intro_line(esc.GENTLE, biz, en=True).lower()
    assert "overdue" in esc.intro_line(esc.STANDARD, biz, en=True).lower()
    assert "past due" in esc.intro_line(esc.FIRM, biz, en=True).lower()
    assert "final" in esc.intro_line(esc.FINAL, biz, en=True).lower()
    # Hinglish variants exist and are non-empty
    for t in (esc.GENTLE, esc.STANDARD, esc.FIRM, esc.FINAL):
        assert esc.intro_line(t, biz, en=False)


def test_closing_line_only_for_firm_and_final():
    assert esc.closing_line(esc.GENTLE, en=True) == ""
    assert esc.closing_line(esc.STANDARD, en=True) == ""
    assert esc.closing_line(esc.FIRM, en=True)
    assert esc.closing_line(esc.FINAL, en=True)


def test_formal_letter_is_firm_not_a_threat():
    letter = esc.formal_letter_text("ACME", "Ramesh Traders", "Rs 50,000", 40, en=True)
    assert "Ramesh Traders" in letter and "50,000" in letter
    assert "40 days past due" in letter
    # invites payment / correction, never threatens legal action
    low = letter.lower()
    assert "already paid" in low
    for bad in ("legal", "court", "lawyer", "police", "seize"):
        assert bad not in low
    # Hinglish letter also builds and stays Latin-script
    hi = esc.formal_letter_text("ACME", "Ramesh", "Rs 5,000", 0, en=False)
    assert "Ramesh" in hi and "dhanyavaad" in hi.lower()
