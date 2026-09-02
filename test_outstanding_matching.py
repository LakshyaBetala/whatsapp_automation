"""Unit tests for the authoritative-outstanding party resolver.

Guards the "stuck on the old balance" bug: a party whose Tally name was renamed,
or differs only by case / extra spaces, MUST still resolve to its existing client
row so the outstanding refresh updates it (instead of freezing it and spawning a
duplicate). Ambiguous collisions must never be guessed.
"""
from types import SimpleNamespace

from app.routers.tally import _make_outstanding_resolver, _norm_ledger_key


def _party(name, guid=None):
    return SimpleNamespace(name=name, tally_guid=guid)


def _clients(*rows):
    """rows: (id, ledger_name, guid) -> the two lookup dicts the resolver needs."""
    by_ledger, by_guid = {}, {}
    for cid, name, guid in rows:
        c = {"id": cid, "tally_ledger_name": name, "tally_guid": guid}
        if name:
            by_ledger[name] = c
        if guid:
            by_guid[guid] = c
    return by_ledger, by_guid


def test_exact_match():
    bl, bg = _clients(("c1", "ASHOK ENTERPRISES", "g1"))
    r = _make_outstanding_resolver(bl, bg, [_party("ASHOK ENTERPRISES", "g1")])
    assert r("ASHOK ENTERPRISES")["id"] == "c1"


def test_rename_matches_by_guid():
    # Client stored under the OLD name; Tally now sends a NEW name with same GUID.
    bl, bg = _clients(("c1", "OLD NAME", "g1"))
    r = _make_outstanding_resolver(bl, bg, [_party("NEW NAME", "g1")])
    assert r("NEW NAME")["id"] == "c1"          # rename-safe, not frozen


def test_case_and_space_fallback():
    bl, bg = _clients(("c1", "VIGNESH AGENCIES   KODUNGAIYAR", "g1"))
    r = _make_outstanding_resolver(bl, bg, [])
    assert r("vignesh agencies kodungaiyar")["id"] == "c1"
    assert r("VIGNESH AGENCIES KODUNGAIYAR")["id"] == "c1"   # single spaces


def test_ambiguous_normalised_collision_not_guessed():
    # Two different ledgers normalise the same -> never guess which.
    bl, bg = _clients(("c1", "A B", "g1"), ("c2", "a  b", "g2"))
    r = _make_outstanding_resolver(bl, bg, [])
    assert r("a b") is None


def test_no_match_returns_none():
    bl, bg = _clients(("c1", "ASHOK", "g1"))
    r = _make_outstanding_resolver(bl, bg, [])
    assert r("SOMEONE ELSE") is None


def test_exact_beats_guid_and_norm():
    # Exact name must win even if another row shares the GUID mapping.
    bl, bg = _clients(("c1", "REAL NAME", "g1"), ("c2", "OTHER", "g2"))
    r = _make_outstanding_resolver(bl, bg, [_party("REAL NAME", "g2")])
    assert r("REAL NAME")["id"] == "c1"


def test_guid_only_when_present_in_payload():
    # No GUID hint in the party list -> falls through to normalised (here, none).
    bl, bg = _clients(("c1", "OLD", "g1"))
    r = _make_outstanding_resolver(bl, bg, [])
    assert r("NEW") is None


def test_norm_key_helper():
    assert _norm_ledger_key("  A   B ") == "a b"
    assert _norm_ledger_key(None) == ""


# ── Lump dating (the "172 days" aging bug) ───────────────────────────────────
# Dating every lump at FY start made a Dec-2025 debt read as ~172 days old (days
# since 1-Apr) instead of its true age, which then drove the cadence, the chase
# ranking and the DSO buckets from the wrong date.
from app.routers.tally import _lump_dates

FY = "2026-04-01"


def _row(vnum, inv, due):
    return (vnum, "ref", 100.0, inv, due)


def test_lump_uses_oldest_billwise_date():
    rows = [_row("b1", "2026-06-10", "2026-07-10"),
            _row("b2", "2025-12-04", "2026-01-03"),   # the real oldest
            _row("b3", "2026-02-01", "2026-03-01")]
    assert _lump_dates(rows, None, FY) == ("2025-12-04", "2026-01-03")


def test_lump_falls_back_to_prior_known_age():
    # No bill-wise rows this refresh, but we already knew this party's oldest
    # bill - that is what carries true age through an FY rollover.
    assert _lump_dates([], ("2025-12-04", "2026-01-03"), FY) == ("2025-12-04", "2026-01-03")


def test_lump_uses_fy_start_only_when_nothing_known():
    assert _lump_dates([], None, FY) == (FY, FY)


def test_billwise_beats_prior():
    rows = [_row("b1", "2025-09-01", "2025-10-01")]
    assert _lump_dates(rows, ("2026-01-01", "2026-02-01"), FY) == ("2025-09-01", "2025-10-01")


def test_lump_never_invents_a_newer_date_than_known():
    # The whole point: the chosen date must not be FY start when older data exists.
    inv, _ = _lump_dates([], ("2025-12-04", "2026-01-03"), FY)
    assert inv < FY
