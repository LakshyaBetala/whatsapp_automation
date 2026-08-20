"""The paired-but-never-synced first-run guard: a read command before the first
Tally sync answers with 'press Tally refresh', not an empty/broken result. Action
commands and greetings are NOT gated."""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import bot


def test_sync_state_and_first_run_help():
    # _sync_state: no tally_syncs rows -> 'never'
    class Q:
        def select(s, *a, **k): return s
        def eq(s, *a, **k): return s
        def order(s, *a, **k): return s
        def limit(s, *a, **k): return s
        def execute(s): return type("R", (), {"data": []})()

    class DB:
        def table(s, n): return Q()

    assert bot._sync_state(DB(), "b1") == "never"
    help_en = bot._first_sync_help("english")
    assert "Tally refresh" in help_en and "LIST" in help_en
    help_hi = bot._first_sync_help("hinglish")
    assert "Tally refresh" in help_hi


def test_needs_sync_set_covers_read_commands_not_actions():
    # Read/summary commands are gated; action commands are not.
    assert "LIST" in bot._NEEDS_SYNC
    assert "DIGEST" in bot._NEEDS_SYNC
    assert "CASH" in bot._NEEDS_SYNC
    for action in ("BILL", "PAID", "STOP", "START", "SUPPORT", "HELP", "MSG"):
        assert action not in bot._NEEDS_SYNC
