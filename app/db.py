"""Supabase client singleton.

The backend uses the SERVICE-ROLE key, which bypasses Row Level Security.
Keep this key server-side only - it must never reach a browser or the agent.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

import httpx
from supabase import Client, create_client

from app.config import settings

log = logging.getLogger(__name__)

# Reads are safe to replay; writes are NOT (a retried insert can double-write).
_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})


class _ResilientTransport(httpx.HTTPTransport):
    """Retry a READ whose connection died under it.

    Supabase closes a pooled connection now and then (and sends HTTP/2 GOAWAY
    under load). httpx surfaces that as RemoteProtocolError, which reached the
    shop as a bare 500 - "Your shop is recognised: Server said 500" - and could
    stall a sync. Replaying an idempotent request is the correct response;
    anything that writes is left to fail loudly rather than risk a double-post.
    """

    def handle_request(self, request):
        attempts = 3 if request.method in _IDEMPOTENT else 1
        last = None
        for i in range(attempts):
            try:
                return super().handle_request(request)
            except (httpx.RemoteProtocolError, httpx.ConnectError,
                    httpx.ReadError, httpx.WriteError) as exc:
                last = exc
                if i + 1 < attempts:
                    log.warning("DB transport hiccup (%s %s), retry %d/%d: %s",
                                request.method, request.url.path, i + 1,
                                attempts - 1, exc)
                    time.sleep(0.15 * (i + 1))
        raise last


def _harden(client: Client) -> Client:
    """Give the DB session its own HTTP/1.1 pool with retrying reads.

    postgrest-py hardcodes http2=True, so EVERY database call in the process
    multiplexes onto a single HTTP/2 connection. Once the route handlers moved
    into FastAPI's threadpool, ~40 threads shared that one connection and
    Supabase's GOAWAY started killing whatever was in flight. HTTP/1.1 gives
    each request an independent pooled connection, so one closed connection can
    never take others down with it.
    """
    try:
        session = client.postgrest.session
        session._transport = _ResilientTransport(
            verify=True,
            http2=False,
            limits=httpx.Limits(max_connections=60, max_keepalive_connections=20),
        )
        session._mounts = {}
    except Exception:
        log.exception("could not harden the DB session - continuing with defaults")
    return client


@lru_cache
def get_client() -> Optional[Client]:
    """Return a cached Supabase client, or None if not configured.

    Returning None (instead of raising) lets the app boot for local UI /
    scheduler-wiring work before Supabase keys are filled in. Callers that
    truly need the DB should use `require_db()`.
    """
    if not settings.supabase_configured:
        log.warning("Supabase not configured - DB calls will be no-ops.")
        return None
    return _harden(create_client(settings.supabase_url,
                                 settings.supabase_service_key))


def require_db() -> Client:
    client = get_client()
    if client is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_KEY in your environment."
        )
    return client
