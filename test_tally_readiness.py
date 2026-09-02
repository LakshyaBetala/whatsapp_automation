"""Tally readiness gate (tally_agent.agent.readiness_decision).

The failure it prevents: ASVA is started FIRST, then Tally is opened. The agent
used to jump on Tally the instant its port answered - before Tally had finished
loading the company into memory - and a big debtor/bill export then crashed Tally
with a Memory Access Violation. The gate only green-lights a heavy read once Tally
answers a tiny company-list probe, THIS company is in the open list, AND a warm-up
grace period has passed. A dropout restarts the warm-up.
"""
import os
import sys

# agent.py uses `from config import ...` / `import tally_xml` (siblings), so it
# must be imported with tally_agent/ on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tally_agent"))
import agent  # noqa: E402
rd = agent.readiness_decision

CO = "Acme Traders"
WARM = 20


def test_tally_not_answering_is_never_ready():
    ready, since = rd(None, CO, None, 100.0, WARM)
    assert ready is False and since is None


def test_up_but_company_not_loaded_yet():
    # Tally's HTTP is up (answers company-list) but our company is still loading.
    ready, since = rd(["Some Other Co"], CO, None, 100.0, WARM)
    assert ready is False and since is None


def test_company_appears_starts_warmup_not_ready_yet():
    ready, since = rd([CO], CO, None, 100.0, WARM)
    assert ready is False           # first sighting -> warm-up begins
    assert since == 100.0


def test_within_warmup_still_waiting():
    ready, since = rd([CO], CO, 100.0, 115.0, WARM)   # 15s < 20s
    assert ready is False and since == 100.0


def test_after_warmup_is_ready():
    ready, since = rd([CO], CO, 100.0, 121.0, WARM)   # 21s >= 20s
    assert ready is True and since == 100.0


def test_dropout_restarts_warmup():
    # Was ready, then Tally vanished (restart / closed): reset so the warm-up runs
    # again next time it reappears - never jump straight onto a reloading Tally.
    ready, since = rd(None, CO, 100.0, 130.0, WARM)
    assert ready is False and since is None


def test_company_match_is_case_and_space_tolerant():
    for name in ["acme traders", "  ACME TRADERS  ", "Acme Traders"]:
        ready, since = rd([name], CO, 100.0, 200.0, WARM)
        assert ready is True


def test_full_asva_first_then_tally_sequence():
    # Simulate the exact reported flow across successive probe ticks.
    since = None
    t = 0.0
    # ASVA running, Tally not open yet: several ticks, never ready.
    for _ in range(3):
        ready, since = rd(None, CO, since, t, WARM); t += 5
        assert not ready and since is None
    # Tally launches; port answers but company still loading.
    ready, since = rd([], CO, since, t, WARM); t += 5
    assert not ready and since is None
    # Company now open -> warm-up starts.
    ready, since = rd([CO], CO, since, t, WARM)
    assert not ready and since == t
    start = since
    # Within warm-up: still holding off the heavy read.
    ready, since = rd([CO], CO, since, start + WARM - 1, WARM)
    assert not ready
    # Warm-up elapsed: NOW it is safe to read heavily.
    ready, since = rd([CO], CO, since, start + WARM + 1, WARM)
    assert ready


# ── Post-crash cooldown ──────────────────────────────────────────────────────
# A heavy read that throws usually means Tally just DIED mid-export. Retrying
# immediately hits a restarting (loading) Tally and crashes it again, in a loop.
# So after any heavy failure the agent must leave Tally completely alone for the
# cooldown, then restart the whole warm-up.
ca = agent.cooldown_active


def test_cooldown_inactive_when_never_failed():
    assert ca(None, 1000.0, 180) is False


def test_cooldown_active_right_after_failure():
    assert ca(1000.0, 1000.5, 180) is True


def test_cooldown_active_just_before_expiry():
    assert ca(1000.0, 1179.0, 180) is True


def test_cooldown_expires_exactly_at_boundary():
    assert ca(1000.0, 1180.0, 180) is False


def test_cooldown_expired_long_after():
    assert ca(1000.0, 5000.0, 180) is False


def test_zero_cooldown_never_blocks():
    assert ca(1000.0, 1000.0, 0) is False


def test_negative_cooldown_treated_as_zero():
    assert ca(1000.0, 1000.0, -5) is False


# ── FY rollover detection ────────────────────────────────────────────────────
# Every April a shop opens next year's Tally company. The configured name then
# matches nothing, the gate says "not ready", and ASVA silently stops syncing.
# Detection must be CONSERVATIVE: suggest only when exactly one open company
# shares the FY-stripped base name.
frc = agent.fy_rollover_candidate
sfs = agent.strip_fy_suffix


def test_strip_fy_suffix_variants():
    assert sfs("SRI SHANKAR AGENCIES - (from 1-Apr-24)") == "SRI SHANKAR AGENCIES"
    assert sfs("SUPREME PETRO CHEMICALS - (25-26)") == "SUPREME PETRO CHEMICALS"
    # a company can accumulate several suffixes over the years
    assert sfs("SURBHI ENTERPRISES - (from 1-Apr-2019) - (from 1-Apr-2020)") == "SURBHI ENTERPRISES"
    assert sfs("RISHAB TRADING COMPANY") == "RISHAB TRADING COMPANY"


def test_rollover_detected_when_year_changes():
    assert frc("ACME - (from 1-Apr-24)", ["ACME - (from 1-Apr-25)"]) == "ACME - (from 1-Apr-25)"


def test_no_rollover_when_company_is_open():
    assert frc("ACME - (from 1-Apr-24)", ["ACME - (from 1-Apr-24)", "OTHER"]) is None


def test_no_suggestion_when_two_candidates_match():
    # Never guess between two plausible rollovers.
    assert frc("ACME - (25-26)", ["ACME - (26-27)", "ACME - (24-25)"]) is None


def test_no_suggestion_for_a_different_company():
    assert frc("ACME - (25-26)", ["ZENITH TRADERS - (26-27)"]) is None


def test_rollover_ignores_case_and_spacing():
    assert frc("Acme  Traders - (25-26)", ["ACME TRADERS - (26-27)"]) == "ACME TRADERS - (26-27)"


def test_no_open_companies_means_no_suggestion():
    assert frc("ACME - (25-26)", []) is None
    assert frc("ACME - (25-26)", None) is None


def test_plain_name_rollover_to_suffixed():
    # Shop had a plain name, then created an FY-suffixed company.
    assert frc("ACME TRADERS", ["ACME TRADERS - (from 1-Apr-26)"]) == "ACME TRADERS - (from 1-Apr-26)"
