"""A route handler that never awaits must not be `async def`.

The Supabase client is synchronous and the backend runs ONE uvicorn worker. An
`async def` handler therefore does its database work ON the event loop, and for
that whole time nothing else in the system is served: not another shop's page,
not the reminder sweep, not the outbox drain, not an inbound webhook.

This is not theoretical. Measured on the live box in October 2026, a /health
check that takes 715ms took 2,927ms while a single Dashboard rendered, because
every admin page was `async def` with no awaits. Declaring them `def` hands
them to FastAPI's threadpool and the loop stays free.

So: if a handler awaits, it may be async. If it does not, it must be `def`.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROUTERS = sorted(pathlib.Path("app/routers").glob("*.py"))

# ops_page is `async def` and does no I/O at all - it returns a constant string.
# It can never block, and several tests drive it with asyncio.run().
ALLOWED = {"ops_page"}


def _blocking_handlers(path: pathlib.Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name in ALLOWED:
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and getattr(d.func, "attr", "") in ("get", "post", "put", "delete", "patch")
            for d in node.decorator_list
        )
        if not is_route:
            continue
        awaits = any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
                     for n in ast.walk(node))
        if not awaits:
            out.append((node.name, node.lineno))
    return out


@pytest.mark.parametrize("path", ROUTERS, ids=lambda p: p.name)
def test_handlers_that_never_await_are_not_async(path):
    bad = _blocking_handlers(path)
    assert not bad, (
        "%s: these handlers are `async def` but never await, so their database "
        "work runs on the event loop and blocks every other request on this "
        "single-worker backend. Declare them `def`:\n%s"
        % (path.name, "\n".join("  %s (line %d)" % b for b in bad))
    )


def test_the_check_can_actually_fail():
    """A guard that cannot fail guards nothing."""
    import tempfile

    src = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/x')\n"
        "async def handler():\n"
        "    return db.table('t').select('*').execute()\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fake.py"
        p.write_text(src, encoding="utf-8")
        assert _blocking_handlers(p) == [("handler", 4)]
