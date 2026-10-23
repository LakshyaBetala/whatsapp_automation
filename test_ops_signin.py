"""Signing in to the Command Center: once per device, and nothing in the URL.

What is behind that page is every shop's data and a Delete that wipes one
permanently, so these pin the parts that are easy to regress: that a stranger
gets nothing, that the key never travels in a URL again, that the cookie
cannot be read by a page script or replayed as the key itself, that guessing
is throttled, and that signing out actually ends it.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

from conftest import maybe_await, ops_request
from app.routers import ops

KEY = "operator-key-for-tests"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", KEY)
    ops._FAILS.clear()
    yield
    ops._FAILS.clear()


def _body(resp) -> str:
    b = getattr(resp, "body", b"") or b""
    return b.decode("utf-8", "replace")


# ── Getting in ──────────────────────────────────────────────────────────
def test_a_stranger_gets_the_login_box_not_the_console():
    html = _body(maybe_await(ops.ops_page(request=ops_request(signed_in=False), key="")))
    assert "Enter the operator key" in html
    assert "Subscriptions" not in html, "the console leaked to someone not signed in"


def test_the_right_key_is_exchanged_for_a_cookie():
    r = ops.ops_login(ops.LoginPayload(key=KEY), ops_request(signed_in=False))
    cookie = r.headers.get("set-cookie", "")
    assert ops.COOKIE in cookie
    assert "HttpOnly" in cookie, "a page script could read the session"
    assert "SameSite=strict" in cookie.replace("samesite", "SameSite")
    assert "Secure" in cookie, "sent over https, so it must be marked Secure"
    assert KEY not in cookie, "the cookie must not BE the key"


def test_the_cookie_cannot_be_replayed_as_the_key():
    """A stolen cookie must not open the write endpoints, which take the key
    itself in their body."""
    assert not ops._key_ok(ops._cookie_value())


def test_a_signed_in_device_gets_the_console():
    html = _body(maybe_await(ops.ops_page(request=ops_request(), key="")))
    assert "Subscriptions" in html and "Command Center" in html


def test_an_old_bookmarked_key_url_is_swapped_for_a_cookie_and_cleaned():
    """The key in a query string is the leak being closed, so arriving with
    one must not simply keep working as it was."""
    r = maybe_await(ops.ops_page(request=ops_request(signed_in=False), key=KEY))
    assert r.status_code == 303
    assert r.headers["location"] == "/ops", "should land on a URL with no key in it"
    assert ops.COOKIE in r.headers.get("set-cookie", "")


# ── Keeping others out ──────────────────────────────────────────────────
def test_a_wrong_key_is_refused_and_counted():
    with pytest.raises(Exception) as e:
        ops.ops_login(ops.LoginPayload(key="nope"), ops_request(signed_in=False))
    assert getattr(e.value, "status_code", None) == 401
    assert ops._FAILS, "a wrong key was not counted towards the lockout"


def test_guessing_is_locked_out():
    req = ops_request(signed_in=False, host="198.51.100.7")
    for _ in range(ops._MAX_FAILS):
        with pytest.raises(Exception):
            ops.ops_login(ops.LoginPayload(key="nope"), req)
    with pytest.raises(Exception) as e:
        ops.ops_login(ops.LoginPayload(key="nope"), req)
    assert getattr(e.value, "status_code", None) == 429

    # The lockout is per address: another device must not be punished for it.
    other = ops_request(signed_in=False, host="198.51.100.8")
    assert ops.ops_login(ops.LoginPayload(key=KEY), other).status_code == 200


def test_signing_in_clears_the_failures():
    req = ops_request(signed_in=False, host="198.51.100.9")
    with pytest.raises(Exception):
        ops.ops_login(ops.LoginPayload(key="nope"), req)
    ops.ops_login(ops.LoginPayload(key=KEY), req)
    assert "198.51.100.9" not in ops._FAILS


def test_signing_out_ends_it():
    cookie = ops.ops_logout().headers.get("set-cookie", "")
    assert ops.COOKIE in cookie and ("Max-Age=0" in cookie or "expires=" in cookie.lower())


def test_nothing_opens_while_the_console_is_switched_off(monkeypatch):
    monkeypatch.setattr(ops.settings, "admin_api_key", "")
    assert not ops._cookie_ok(ops_request())
    assert not ops._key_ok("")
    html = _body(maybe_await(ops.ops_page(request=ops_request(), key="")))
    assert "Command Center is off" in html


# ── Nothing secret in a URL ─────────────────────────────────────────────
def test_the_page_never_puts_the_key_in_a_url():
    html = _body(maybe_await(ops.ops_page(request=ops_request(), key="")))
    assert "?key=" not in html, "a request with the key in its URL would be logged"
    assert "location.search" not in html, "the page must not read a key off the URL"
    # It still has the key for its POST bodies, which are not logged.
    assert KEY in html


def test_the_login_box_posts_the_key_rather_than_navigating_with_it():
    html = _body(maybe_await(ops.ops_page(request=ops_request(signed_in=False), key="")))
    assert "/ops/login" in html
    assert "location.href='/ops?key=" not in html
