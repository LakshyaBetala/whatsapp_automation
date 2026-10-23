"""Every URL the pages call must be a route that exists.

The screens are built as Python strings, so a renamed or mistyped endpoint is
invisible: the page still renders, the button still looks fine, and the click
quietly 404s. This walks every admin page, pulls out every URL its JavaScript
fetches or navigates to, and checks each one against the app's own routing
table. It also checks the reverse direction loosely: an admin POST endpoint
that nothing calls is worth knowing about.
"""
from __future__ import annotations

import re
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from app.main import app
from app.routers import admin
from test_admin_render import FakeDB, _ok, maybe_await

PAGES = ["admin_page", "admin_today", "admin_payments", "admin_reminders",
         "admin_analytics", "admin_accounts", "admin_setup"]

# Matches fetch('/admin/x'), fetch("/admin/x"), location.href = '/admin?...'
URL_RE = re.compile(r"""['"](/(?:admin|license|ops|pay|m)/[A-Za-z0-9/_\-]*)['"?]""")


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(admin, "require_db", lambda: FakeDB())


def _routes() -> set[str]:
    out = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        if path:
            out.add(path.rstrip("/") or "/")
    return out


def _called_urls(html: str) -> set[str]:
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    found = set()
    for js in scripts:
        for m in URL_RE.finditer(js):
            found.add(m.group(1).rstrip("/") or "/")
    return found


@pytest.mark.parametrize("page", PAGES)
def test_every_url_the_page_calls_is_a_real_route(page):
    html = _ok(maybe_await(getattr(admin, page)(token="tok", lang="english")))
    routes = _routes()
    missing = sorted(u for u in _called_urls(html) if u not in routes)
    assert not missing, (
        "%s calls URLs that are not routes - those buttons 404 silently:\n  %s"
        % (page, "\n  ".join(missing))
    )


def test_the_party_page_too():
    html = _ok(maybe_await(admin.admin_party(token="tok", client_id="c1", lang="english")))
    routes = _routes()
    missing = sorted(u for u in _called_urls(html) if u not in routes)
    assert not missing, "the party page calls: %s" % missing


def test_the_check_would_catch_a_renamed_endpoint():
    """A guard that cannot fail guards nothing."""
    js = "<script>fetch('/admin/this-was-renamed', {method:'POST'})</script>"
    assert "/admin/this-was-renamed" in _called_urls(js)
    assert "/admin/this-was-renamed" not in _routes()
