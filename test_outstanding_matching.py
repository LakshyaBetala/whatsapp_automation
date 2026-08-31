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
