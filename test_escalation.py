"""Owner-approved escalation: tier_for is a severity LABEL only (never auto-changes
a customer message); the owner-triggered formal letter is firm but never a threat."""
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


def test_no_auto_tone_helpers_exist():
    # Guard the product decision: there must be NO function that auto-escalates a
    # customer message's tone. Only the owner-triggered letter escalates.
    assert not hasattr(esc, "intro_line")
    assert not hasattr(esc, "closing_line")


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
