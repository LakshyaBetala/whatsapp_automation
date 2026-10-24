"""One QR, and it belongs to the server.

There used to be two. The shop's app ran its own wa_service on :3001 and the
WhatsApp button showed that QR, while the shop's real session sat waiting on
ASVA's server on a port the app had never heard of. So every shop that scanned
landed on laptop transport, which only delivers while that computer is awake
and linked - and whose queue then expires unsent. All twelve did. SUPREME
queued 313 messages and delivered ten.

A scanned session cannot be moved afterwards: Baileys binds it to the device
that completed the pairing. So the fix is not to transfer anything, it is to
show the SERVER's QR in the app, and let the scan create the session there.
The app became a display.

These tests pin the two halves that must never drift back:
the app must not start a competing local session, and the status it shows must
come from the server.
"""
from __future__ import annotations

import pathlib
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

DESKTOP = pathlib.Path(__file__).parent / "desktop"
MAIN = (DESKTOP / "main.js").read_text(encoding="utf-8")
PRELOAD = (DESKTOP / "preload.js").read_text(encoding="utf-8")
RENDERER = (DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")


# ── the app must not run a second session ────────────────────────────────
def test_the_local_whatsapp_service_is_not_started():
    """Two processes on one number is a 440 connectionReplaced, which the
    owner experiences as 'it keeps disconnecting'."""
    assert "filter((n) => n !== 'whatsapp')" in MAIN, \
        "the app is starting its own WhatsApp service again"


def test_nothing_reads_a_whatsapp_status_from_this_computer():
    leftovers = re.findall(r"localhost:3001[^\s'\"]*", MAIN + RENDERER)
    assert leftovers == [], f"still talking to the laptop session: {leftovers}"


def test_the_qr_comes_from_the_backend():
    assert "/admin/whatsapp/status?token=" in MAIN
    assert "function waStatus(" in MAIN


def test_a_shop_with_no_session_yet_asks_for_one():
    """A brand new shop has no reserved port, so the first poll must create it
    rather than show an empty box forever."""
    assert "'not_started'" in MAIN
    assert "/admin/whatsapp/start" in MAIN


def test_relink_goes_through_the_backend():
    """The child binds 127.0.0.1 on the VPS, so the shop cannot reach it."""
    assert "waRelink" in PRELOAD and "wa-relink" in MAIN
    assert "/admin/whatsapp/relink" in MAIN
    assert "window.asva.waRelink()" in RENDERER


def test_the_remote_call_gets_a_longer_timeout_than_localhost_did():
    """2.5s was fine for a loopback call and is not fine for a shop uplink."""
    assert re.search(r"waStatus[\s\S]{0,2000}?\}, 12000\);", MAIN), \
        "the server status call still uses the localhost timeout"


# ── the backend half ──────────────────────────────────────────────────────
def test_the_status_endpoint_reports_the_three_phases():
    from app.services.wa_server import phase
    assert phase(None) == "starting"
    assert phase({}) == "starting"
    assert phase({"qr": "data:image/png;base64,xxx"}) == "qr"
    assert phase({"ready": True}) == "connected"
    assert phase({"ready": True, "qr": "stale"}) == "connected", \
        "a connected session must never still offer a QR"


def test_connecting_flips_the_shop_onto_server_transport():
    import inspect

    from app.routers import admin
    src = inspect.getsource(admin.admin_whatsapp_status)
    assert "mark_connected" in src, "a successful scan must move the shop to the server"


def test_relink_leaves_transport_alone():
    """Flipping to 'laptop' here would hand the queue to a computer that no
    longer runs wa_service, so every send would fail until the re-scan."""
    import inspect

    from app.routers import admin
    src = inspect.getsource(admin.admin_whatsapp_relink)
    assert '"wa_transport": "laptop"' not in src
    assert '"wa_ready": False' in src, "a dead session must not still read as ready"


def test_relink_needs_the_shop_token():
    """It wipes a live WhatsApp link, so it must not be callable unauthenticated."""
    import inspect

    from app.routers import admin
    sig = inspect.signature(admin.admin_whatsapp_relink)
    assert "payload" in sig.parameters
    src = inspect.getsource(admin.admin_whatsapp_relink)
    assert "_wa_biz(payload.token)" in src


def test_the_child_is_never_exposed_beyond_the_box():
    """Every shop session binds loopback and demands the key; the backend is
    the only thing that can reach one."""
    wa = (pathlib.Path(__file__).parent / "wa_service" / "manager.js").read_text(encoding="utf-8")
    assert "WA_BIND_HOST: '127.0.0.1'" in wa
    assert "WA_API_KEY," in wa

    from app.services import wa_server
    assert "127.0.0.1" in wa_server.child_url(4101)


def test_the_shop_list_is_localhost_only_and_keyed():
    """It hands out agent tokens, so it must refuse anything off-box - including
    a request that arrived through the Cloudflare tunnel, which connects from
    localhost and so would otherwise look local."""
    from app.services.wa_server import manager_request_allowed
    from app.config import settings

    key = (settings.wa_api_key or "").strip()
    if not key:
        pytest.skip("WA_API_KEY not set in this environment")
    good = {"x-asva-key": key}
    assert manager_request_allowed("127.0.0.1", good) is True
    assert manager_request_allowed("10.0.0.5", good) is False
    assert manager_request_allowed("127.0.0.1", {"x-asva-key": "wrong"}) is False
    assert manager_request_allowed(
        "127.0.0.1", {**good, "cf-connecting-ip": "1.2.3.4"}) is False
