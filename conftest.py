"""Shared pytest helpers.

`maybe_await` lets a test invoke a FastAPI route handler without caring whether
it is declared `async def` or plain `def`. Handlers that only do BLOCKING work
(the synchronous Supabase client) are deliberately plain `def` so FastAPI runs
them in its threadpool instead of stalling the single event loop - see
app/routers/*. That declaration is a performance decision and may change per
handler, so tests should not hard-code it.
"""
from __future__ import annotations

import asyncio
import inspect


def maybe_await(value):
    """Run `value` to completion if it is a coroutine, else return it as-is."""
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def ops_request(*, signed_in: bool = True, host: str = "203.0.113.9",
                scheme: str = "https"):
    """A request to the Command Center, as a signed-in device or a stranger.

    /ops is gated on an HttpOnly cookie rather than a key in the URL (a key in
    a query string lands in history, screenshots and the server's log), so a
    test that wants the page has to arrive carrying one.
    """
    from starlette.requests import Request

    from app.routers import ops as _ops

    headers = [(b"host", b"app.tryasva.com"), (b"cf-connecting-ip", host.encode())]
    if signed_in:
        headers.append((b"cookie", f"{_ops.COOKIE}={_ops._cookie_value()}".encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": scheme,
        "path": "/ops", "raw_path": b"/ops", "query_string": b"",
        "root_path": "", "headers": headers, "client": ("127.0.0.1", 1234),
        "server": ("app.tryasva.com", 443),
    })
