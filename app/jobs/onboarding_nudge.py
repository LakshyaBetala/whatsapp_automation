"""Onboarding nudge job: chase shops that were paired but never synced.

Runs hourly on the owner-facing deployment (the bot/host). Finds shops paired in
the last week that have loaded no Tally data yet and messages the owner once to
open ASVA + Refresh before the setup code lapses. All logic + dedup lives in
app.services.assistant.nudge_unsynced; this is just the scheduler entry point.
"""
from __future__ import annotations

import logging

from app.db import require_db
from app.services import assistant

log = logging.getLogger(__name__)


async def run() -> None:
    db = require_db()
    await assistant.nudge_unsynced(db)
