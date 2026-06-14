"""Supabase keep-alive ping — prevents free-tier project from pausing.

Hits our own /health endpoint every 6 hours so Supabase sees activity.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


async def ping() -> None:
    """Ping our own health endpoint to keep Supabase awake."""
    url = f"{settings.public_base_url}/health"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            log.info("Keep-alive ping: %s → %s", url, resp.status_code)
    except Exception as exc:
        # Not critical — log and move on
        log.warning("Keep-alive ping failed: %s", exc)
