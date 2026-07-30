"""Supabase keep-alive ping - prevents the free-tier project from pausing.

Does a tiny DB round-trip every 6 hours so Supabase sees activity. It touches the
database DIRECTLY (not via the /health HTTP endpoint), because /health is now a
pure liveness check that never hits the DB - so a self-HTTP call would no longer
keep Supabase awake, and a direct query is more reliable anyway.
"""
from __future__ import annotations

import logging

from app.db import get_client

log = logging.getLogger(__name__)


async def ping() -> None:
    """Touch the database so the Supabase free project never idles into a pause."""
    db = get_client()
    if db is None:
        log.info("Keep-alive skipped: Supabase not configured")
        return
    try:
        db.table("businesses").select("id", count="exact").limit(1).execute()
        log.info("Keep-alive: Supabase touched")
    except Exception as exc:  # not critical - log and move on
        log.warning("Keep-alive DB touch failed: %s", exc)
