"""Exhaustive checks for Indian phone matching (app/services/phones.py).

This is the code that decides whether an inbound WhatsApp reply belongs to a
registered party, so it is tested hard: every real-world format, every reject
case, and same-person matching across formats. Parametrised so the case count
is well into the hundreds.
"""
import pytest

from app.services import phones

# The canonical 10-digit cores we test with (first digit 6/7/8/9).
CORES = ["9812345678", "9000000001", "8123456789", "7011122233",
         "6300000000", "9999999999", "7400000000", "8888888888"]

# Every equivalent way each core shows up in the wild -> all must normalise to 91+core.
def _forms(core):
    return [
        core,                              # 10-digit
        "0" + core,                        # STD 0-prefixed
        "91" + core,                       # 91-prefixed
        "0091" + core,                     # 0091-prefixed
        "+91" + core,                      # +91
        "+91 " + core[:5] + " " + core[5:],  # +91 with a space
        "+91-" + core[:5] + "-" + core[5:],  # dashed
        "(+91) " + core,                   # bracketed cc
        " 91" + core + " ",                # padded
        "91-" + core,                      # 91 dashed
        "91 " + core,                      # 91 spaced
        "91" + core + "@s.whatsapp.net",   # WhatsApp JID
        "0091 " + core,                    # 0091 spaced
    ]


@pytest.mark.parametrize("core", CORES)
def test_all_formats_normalize_to_the_same_canonical(core):
    want = "91" + core
    for raw in _forms(core):
        assert phones.normalize(raw) == want, f"{raw!r} -> {phones.normalize(raw)!r}, want {want!r}"


@pytest.mark.parametrize("core", CORES)
def test_last10_is_the_core_for_every_format(core):
    for raw in _forms(core):
        assert phones.last10(raw) == core, f"{raw!r}"


@pytest.mark.parametrize("core", CORES)
def test_core10_recovers_the_core_for_every_format(core):
    for raw in _forms(core):
        assert phones.core10(raw) == core, f"{raw!r}"


# ── Things that must be REJECTED (not an Indian mobile) ─────────────────────
REJECTS = [
    None, "", "   ", "abc", "+", "-", "@s.whatsapp.net",
    "12345",                 # too short
    "123456789",             # 9 digits
    "5123456789",            # 10 digits but starts 5 (invalid mobile)
    "1234567890",            # starts 1
    "0123456789",            # 11 digits, core starts 1 after stripping 0
    "044229876543",          # a landline-ish 12-digit not 91-prefixed
    "918012345",             # 91 + too few
    "9",                     # single digit
    "00000000000",           # 11 zeros
    "9199999",               # 91 + short
    "912345678901234",       # far too long
    "hello world",
    "91",                    # just the country code
    "091234",                # short 0-prefixed
]


@pytest.mark.parametrize("raw", REJECTS)
def test_invalid_numbers_return_none_and_no_false_match(raw):
    assert phones.normalize(raw) is None, f"{raw!r} should not normalise"
    assert phones.core10(raw) is None, f"{raw!r} should have no core"


# ── same_number: same person across formats == True ─────────────────────────
@pytest.mark.parametrize("core", CORES)
def test_same_number_true_across_all_format_pairs(core):
    forms = _forms(core)
    # every form matches every other form (all the same person)
    for i in range(len(forms)):
        for j in range(len(forms)):
            assert phones.same_number(forms[i], forms[j]) is True, \
                f"{forms[i]!r} vs {forms[j]!r}"


def test_same_number_false_for_different_people():
    # different cores never match, in any format combo
    a_forms = _forms("9812345678")
    b_forms = _forms("9812345679")   # last digit differs
    for a in a_forms:
        for b in b_forms:
            assert phones.same_number(a, b) is False, f"{a!r} vs {b!r}"


@pytest.mark.parametrize("bad", REJECTS)
def test_same_number_never_matches_junk(bad):
    assert phones.same_number(bad, "919812345678") is False
    assert phones.same_number("919812345678", bad) is False
    assert phones.same_number(bad, bad) is False


def test_two_different_valid_numbers_are_distinct():
    for i in range(len(CORES)):
        for j in range(len(CORES)):
            expect = (i == j)
            assert phones.same_number("91" + CORES[i], "91" + CORES[j]) is expect
