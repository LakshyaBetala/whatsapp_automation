"""Supabase client singleton.

The backend uses the SERVICE-ROLE key, which bypasses Row Level Security.
Keep this key server-side only — it must never reach a browser or the agent.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from supabase import Client, create_client

from app.config import settings

log = logging.getLogger(__name__)


@lru_cache
def get_client() -> Optional[Client]:
    """Return a cached Supabase client, or None if not configured.

    Returning None (instead of raising) lets the app boot for local UI /
    scheduler-wiring work before Supabase keys are filled in. Callers that
    truly need the DB should use `require_db()`.
    """
    if not settings.supabase_configured:
        log.warning("Supabase not configured — DB calls will be no-ops.")
        return None
    return create_client(settings.supabase_url, settings.supabase_service_key)


def require_db() -> Client:
    client = get_client()
    if client is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_KEY in your environment."
        )
    return client
