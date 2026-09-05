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
