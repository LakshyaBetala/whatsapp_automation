"""Reconcile / archive of parties deleted in Tally (tally._run_reconcile).

The safety contract, proven here:
  * a Tally party missing from the current Tally list is soft-archived
    (archived_at + excluded + reminders off) - never hard-deleted;
  * a NON-Tally (manually added) party is NEVER touched;
  * a trivial name difference (space/case) or a GUID match is NOT archived;
  * an incomplete Tally list (partial export) aborts the whole archive;
  * a party that reappears in Tally is restored.
"""
import sys
import types
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.routers import tally


class _Upd:
    def __init__(self, store, patch):
        self._store = store
        self._patch = patch

    def in_(self, _field, ids):
        for i in ids:
            self._store.setdefault(i, {}).update(self._patch)
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=[])


class _Tbl:
    def __init__(self, store):
        self._store = store

    def update(self, patch):
        return _Upd(self._store, patch)


class FakeDB:
    def __init__(self):
        self.updates = {}   # client_id -> merged patch applied

    def table(self, name):
        assert name == "clients"
        return _Tbl(self.updates)


def _c(cid, ledger, guid=None, archived=None):
    return {"id": cid, "tally_ledger_name": ledger, "tally_guid": guid, "archived_at": archived}


def _party(name, guid=None):
    return types.SimpleNamespace(name=name, tally_guid=guid)


BIZ = "b1"


def test_archives_party_missing_from_tally():
    db = FakeDB()
    clients = [_c("1", "Alpha"), _c("2", "Beta"), _c("3", "Gamma")]
    # Tally now only has Alpha + Beta; Gamma was deleted.
    n = tally._run_reconcile(db, BIZ, clients, ["Alpha", "Beta"], [])
    assert n == 1
    assert db.updates["3"]["archived_at"] is not None
    assert db.updates["3"]["excluded"] is True
    assert db.updates["3"]["reminders_enabled"] is False
    assert "1" not in db.updates and "2" not in db.updates   # survivors untouched


def test_never_touches_non_tally_party():
    db = FakeDB()
    clients = [_c("1", "Alpha"), _c("2", None)]   # #2 is manually added (no ledger)
    tally._run_reconcile(db, BIZ, clients, ["Alpha"], [])
    assert "2" not in db.updates                   # non-Tally party is safe


def test_normalised_name_is_not_archived():
    db = FakeDB()
    clients = [_c("1", "Alpha"), _c("2", " beta  traders ")]
    # Tally lists it with different spacing/case - must be treated as present.
    n = tally._run_reconcile(db, BIZ, clients, ["Alpha", "BETA TRADERS"], [])
    assert n == 0 and db.updates == {}


def test_guid_match_is_not_archived():
    db = FakeDB()
    clients = [_c("1", "Alpha"), _c("2", "Old Name", guid="G2")]
    # Renamed in Tally: name differs, but the GUID is still present.
    parties = [_party("Alpha"), _party("New Name", guid="G2")]
    n = tally._run_reconcile(db, BIZ, clients, ["Alpha", "New Name"], parties)
    assert n == 0 and db.updates == {}


def test_partial_export_aborts_archive():
    db = FakeDB()
    clients = [_c(str(i), "P%d" % i) for i in range(10)]
    # Tally returned only 2 of 10 known parties - a partial/failed export.
    n = tally._run_reconcile(db, BIZ, clients, ["P0", "P1"], [])
    assert n == 0 and db.updates == {}            # never wipe on a suspect list


def test_reappeared_party_is_restored():
    db = FakeDB()
    clients = [_c("1", "Alpha"), _c("2", "Beta", archived="2026-08-01T00:00:00Z")]
    # Beta is archived but is back in Tally now.
    n = tally._run_reconcile(db, BIZ, clients, ["Alpha", "Beta"], [])
    assert n == 0                                  # nothing newly archived
    assert db.updates["2"]["archived_at"] is None  # restored
    assert db.updates["2"]["excluded"] is False


def test_no_tally_clients_is_noop():
    db = FakeDB()
    assert tally._run_reconcile(db, BIZ, [_c("1", None)], [], []) == 0
    assert db.updates == {}
