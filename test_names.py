"""Party-name intelligence (app/services/names.py).

Two pure jobs, so we hammer them hard: clean_display (messy Tally ledger name ->
the name the owner uses) and resolve (a name the owner TYPED -> one/many/none),
which must forgive case/typos/partials WITHOUT over-matching. A wrong guess on
someone's dues is the exact bad experience we are removing, so the "ask when
unsure" behaviour is tested as carefully as the confident-hit behaviour.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import names


# ── clean_display ───────────────────────────────────────────────────────────
def test_clean_display_strips_company_prefix():
    assert names.clean_display("M/S RAMESH TRADERS") == "Ramesh Traders"
    assert names.clean_display("m/s ramesh traders") == "Ramesh Traders"
    assert names.clean_display("MESSRS. SURESH & SONS") == "Suresh & Sons"
    assert names.clean_display("Shri Ganesh Stores") == "Ganesh Stores"
    assert names.clean_display("MR. RAKESH KUMAR") == "Rakesh Kumar"


def test_clean_display_strips_only_delivery_rounds():
    # The ONLY trailing tag treated as noise is the delivery-round (RTE/ROUTE n).
    assert names.clean_display("RAMESH TRADERS-RTE4") == "Ramesh Traders"
    assert names.clean_display("RAMESH TRADERS - ROUTE 12") == "Ramesh Traders"
    # combined: prefix + route
    assert names.clean_display("M/S RAMESH TRADERS-RTE4") == "Ramesh Traders"


def test_clean_display_keeps_distinguishing_identity():
    # Area / branch / suffix is IDENTITY, never stripped - two same-named parties
    # must stay tellable apart.
    assert names.clean_display("MEENAKSHI ELECTRICALS KLP") == "Meenakshi Electricals KLP"
    assert names.clean_display("Meenakshi Electrical Aratur") == "Meenakshi Electrical Aratur"
    assert names.clean_display("SUNIL AGENCIES (KOVUR)") == "Sunil Agencies (Kovur)"
    assert names.clean_display("Ramesh Traders - A12") == "Ramesh Traders - A12"


def test_clean_display_keeps_acronyms_and_ampersand():
    assert names.clean_display("ABC ENTERPRISES") == "ABC Enterprises"
    assert names.clean_display("SKF BEARINGS") == "SKF Bearings"
    assert names.clean_display("raj & co") == "Raj & Co"


def test_clean_display_never_empty():
    # nothing but a prefix -> fall back to the original, never blank
    assert names.clean_display("M/S") != ""
    assert names.clean_display("") == ""
    assert names.clean_display("   ") == ""


def test_clean_display_does_not_eat_legit_names():
    # a plain two-word name with no tags survives untouched (just title-cased)
    assert names.clean_display("GOPAL STORES") == "Gopal Stores"
    assert names.clean_display("new india hardware") == "New India Hardware"


# ── core / normalize (the match key) ────────────────────────────────────────
def test_core_lines_up_prefixed_and_plain():
    assert names.core("M/S RAMESH TRADERS") == names.core("ramesh traders")
    assert names.core("Messrs Ramesh Traders") == "ramesh traders"
    assert names.core("The Gupta Store") == "gupta store"


# ── resolve: confident single hit ───────────────────────────────────────────
CANDS = ["M/S RAMESH TRADERS-RTE4", "Suresh Textiles", "Gopal Stores",
         "Ram Kumar Agencies", "New India Hardware"]


def _pick(query, cands=CANDS):
    r = names.resolve(query, cands)
    return r["status"], (cands[r["index"]] if r["status"] == "one" else
                         [cands[i] for i in r.get("indices", [])])


def test_resolve_exact_core_is_one():
    st, hit = _pick("ramesh traders")
    assert st == "one" and hit == "M/S RAMESH TRADERS-RTE4"


def test_resolve_case_insensitive_one():
    assert _pick("SURESH TEXTILES")[0] == "one"
    assert _pick("gopal stores")[1] == "Gopal Stores"


def test_resolve_leading_fragment_is_one():
    st, hit = _pick("ramesh tr")
    assert st == "one" and hit == "M/S RAMESH TRADERS-RTE4"
    st2, hit2 = _pick("suresh")
    assert st2 == "one" and hit2 == "Suresh Textiles"


def test_resolve_single_typo_is_forgiven():
    # 'gopla' -> 'gopal' (one transposition ~ edit distance) still resolves
    st, hit = _pick("gopal stroes")
    assert st == "one" and hit == "Gopal Stores"


def test_resolve_prefixed_query_matches_prefixed_name():
    st, hit = _pick("m/s ramesh")
    assert st == "one" and hit == "M/S RAMESH TRADERS-RTE4"


# ── resolve: ask when genuinely unsure (never guess) ────────────────────────
def test_resolve_ambiguous_returns_many():
    # "ram" is a leading fragment of BOTH "Ramesh" and "Ram Kumar" -> ask
    st, hits = _pick("ram")
    assert st == "many"
    assert "M/S RAMESH TRADERS-RTE4" in hits and "Ram Kumar Agencies" in hits


def test_resolve_single_letter_never_confident():
    # one character must never confidently resolve to a party
    st, _ = _pick("r")
    assert st in ("many", "none")
    assert names.resolve("r", CANDS)["status"] != "one"


def test_resolve_no_match_is_none():
    assert _pick("zzz electronics")[0] == "none"
    assert names.resolve("xyzzy", CANDS)["status"] == "none"


def test_resolve_empty_query_is_none():
    assert names.resolve("", CANDS)["status"] == "none"
    assert names.resolve("   ", CANDS)["status"] == "none"


# ── resolve: does not over-match the old-substring way ──────────────────────
def test_resolve_no_spurious_substring_hit():
    # "india" appears inside "New India Hardware"; a bare substring test would
    # also (wrongly) fire on nothing else here, but importantly "in" (2 chars,
    # inside many words) must NOT confidently pick a single party.
    st, _ = _pick("in")
    assert st in ("many", "none")


def test_resolve_full_unique_name_beats_partial_sibling():
    cands = ["Ramesh Traders", "Ramesh Electricals"]
    # exact full name of one sibling -> that one, not "many"
    r = names.resolve("ramesh electricals", cands)
    assert r["status"] == "one" and cands[r["index"]] == "Ramesh Electricals"
    # just "ramesh" -> ambiguous between the two
    assert names.resolve("ramesh", cands)["status"] == "many"


def test_resolve_indices_are_ranked_best_first():
    cands = ["Ramesh Traders", "Rameshwar Oil", "Suresh Metals"]
    r = names.resolve("ramesh", cands)
    assert r["status"] in ("one", "many")
    if r["status"] == "many":
        # best (closest to the typed word) should come first
        assert cands[r["indices"][0]] == "Ramesh Traders"


# ── a realistic Tally-style shop (messy names) end to end ────────────────────
REAL = ["M/S GANESH ENTERPRISES-RTE 1", "SHRI BALAJI TRADING CO",
        "Kumar Provision Stores", "MESSRS A K AGENCIES", "New Deepak Hardware (Main)"]


def test_real_shop_display_names():
    disp = [names.clean_display(n) for n in REAL]
    assert disp == ["Ganesh Enterprises", "Balaji Trading Co",
                    "Kumar Provision Stores", "A K Agencies", "New Deepak Hardware (Main)"]


# ── same-name families: different parties that differ by area/branch ─────────
FAMILY = ["Meenakshi Electricals KLP", "Meenakshi Electricals & Hardware",
          "Meenakshi Electrical Aratur"]


def test_same_stem_different_area_stays_distinct():
    # None of them collapse to the same display name - identity preserved.
    disp = [names.clean_display(n) for n in FAMILY]
    assert len(set(disp)) == 3


def test_typing_the_shared_stem_asks_which_one():
    r = names.resolve("meenakshi", FAMILY)
    assert r["status"] == "many"
    assert len(r["indices"]) == 3               # all three offered, none guessed


def test_typing_the_area_resolves_to_one():
    # The distinguishing word picks the exact party.
    assert FAMILY[names.resolve("meenakshi aratur", FAMILY)["index"]] == "Meenakshi Electrical Aratur"
    assert FAMILY[names.resolve("meenakshi klp", FAMILY)["index"]] == "Meenakshi Electricals KLP"


def test_distinguisher_highlights_the_area():
    d = names.distinguisher(FAMILY)
    assert d == ["Electricals KLP", "Electricals & Hardware", "Electrical Aratur"]


def test_distinguisher_falls_back_when_no_shared_stem():
    d = names.distinguisher(["Gopal Stores", "Suresh Textiles"])
    assert d == ["Gopal Stores", "Suresh Textiles"]


# ── true duplicates: the SAME party entered twice ───────────────────────────
def test_duplicate_groups_flags_only_exact_same_party():
    rows = ["M/S MEENAKSHI ELECTRICALS ARATUR",   # 0
            "Meenakshi Electricals Aratur",        # 1  same party, prefix/case
            "Meenakshi Electricals KLP",           # 2  different area -> NOT a dup
            "Gopal Stores"]                        # 3
    groups = names.duplicate_groups(rows)
    assert groups == [[0, 1]]                       # only 0 and 1, never 2


def test_duplicate_groups_empty_when_all_distinct():
    assert names.duplicate_groups(FAMILY) == []


def test_real_shop_owner_typed_lookups():
    assert REAL[names.resolve("ganesh", REAL)["index"]] == "M/S GANESH ENTERPRISES-RTE 1"
    assert REAL[names.resolve("balaji", REAL)["index"]] == "SHRI BALAJI TRADING CO"
    assert REAL[names.resolve("deepak", REAL)["index"]] == "New Deepak Hardware (Main)"
    assert REAL[names.resolve("kumar prov", REAL)["index"]] == "Kumar Provision Stores"
    assert names.resolve("zzz", REAL)["status"] == "none"
