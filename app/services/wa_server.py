"""Shop WhatsApp on ASVA's server.

A shop moved here runs its own copy of wa_service/index.js (the same,
field-hardened code a shop laptop runs) on a localhost port. The processes are
started and supervised by wa_service/manager.js, which asks the backend for
the list (GET /internal/wa-shops). This module is the backend's side of it:
which port a shop gets, how to read its status and QR, and the switch that
makes the server - not the laptop - deliver the shop's queue.

Every shop process binds 127.0.0.1 and requires the WA_API_KEY header, so
nothing here is reachable from outside the box.
"""
from __future__ import annotations

import datetime as _dt
import hmac
import logging

import httpx

from app.config import settings
from app.services import subscription as subs

log = logging.getLogger(__name__)

# Headers a request carries when it came through the Cloudflare tunnel (which
# connects from localhost, so the source address alone proves nothing).
_TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def allocate_port(used: set, base: int | None = None) -> int:
    p = base if base is not None else settings.wa_shops_base_port
    while p in used:
        p += 1
    return p


def child_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def phase(status: dict | None) -> str:
    """What the owner should see: still starting, a QR to scan, or connected.

    "banned" outranks everything. WhatsApp has blocked the number, the child
    has stopped trying, and showing a QR would send the owner to scan
    something that cannot possibly work.
    """
    if not status:
        return "starting"
    if status.get("banned"):
        return "banned"
    if status.get("ready"):
        return "connected"
    if status.get("qr"):
        return "qr"
    return "starting"


def _headers() -> dict:
    key = (settings.wa_api_key or "").strip()
    return {"x-asva-key": key} if key else {}


async def status(port: int | None) -> dict | None:
    """The shop process's own status ({ready, qr, registered, last_close_code}),
    or None when it is not running (yet)."""
    if not port:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            r = await http.get(f"{child_url(port)}/api/wa/status", headers=_headers())
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def status_sync(port: int | None) -> dict | None:
    """status() for plain `def` route handlers (they run in FastAPI's thread
    pool; an async handler doing sync DB calls would block the event loop)."""
    if not port:
        return None
    try:
        r = httpx.get(f"{child_url(port)}/api/wa/status", headers=_headers(), timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def request_server(db, business_id: str) -> int:
    """The owner chose to run WhatsApp on the server: give the shop a port (once)
    and flag it so the manager starts its process within a minute."""
    rows = (db.table("businesses").select("id, wa_port, wa_server_requested_at")
            .execute()).data or []
    me = next((r for r in rows if r.get("id") == business_id), {}) or {}
    port = me.get("wa_port")
    if not port:
        used = {int(r["wa_port"]) for r in rows if r.get("wa_port")}
        port = allocate_port(used)
    db.table("businesses").update({
        "wa_port": port,
        "wa_server_requested_at": me.get("wa_server_requested_at") or _now_iso(),
    }).eq("id", business_id).execute()
    return int(port)


def mark_connected(db, business_id: str, *, already_linked: bool) -> None:
    """The shop's server WhatsApp is linked: from now on the server delivers its
    queue and the laptop is handed nothing (no double sends)."""
    patch = {"wa_transport": "server", "wa_ready": True, "wa_checked_at": _now_iso()}
    if not already_linked:
        patch["wa_linked_at"] = _now_iso()       # warm-up counts from here
    db.table("businesses").update(patch).eq("id", business_id).execute()


def release_server(db, business_id: str, port: int | None) -> bool:
    """Take this shop's WhatsApp off the server, at the owner's request.

    Logs the number out (so ASVA leaves the phone's Linked devices and the
    session cannot be reused), then clears the request flag so the manager
    stops the process, and points sending back at the shop's own computer.
    Returns whether the child confirmed the logout; the flags are cleared
    either way, because the owner asked to leave and a down child must not
    trap them on the server.
    """
    ok = False
    if port:
        try:
            r = httpx.post(f"{child_url(int(port))}/api/wa/logout",
                           headers=_headers(), timeout=20.0)
            ok = r.status_code == 200
        except Exception:
            log.info("wa logout: child on port %s did not answer", port)
    db.table("businesses").update({
        "wa_transport": "laptop",       # the shop's own app sends again
        "wa_server_requested_at": None,  # the manager stops this shop's process
        "wa_ready": False,
        "wa_checked_at": _now_iso(),
    }).eq("id", business_id).execute()
    return ok


def shops_for_manager(db) -> list:
    """The shop processes the manager should run.

    A LINKED shop runs permanently: that session is how its bills and reminders
    go out, and how its customers' replies come back.

    An UNLINKED shop runs only while someone is actually looking at the QR.
    A Baileys process costs ~86 MB whether or not a session exists, and before
    this a shop that reserved a port and never scanned burned that much
    indefinitely - RISHAB held 86 MB for five days showing a QR nobody was
    watching. Opening the WhatsApp page refreshes wa_server_requested_at on
    every poll, so the window stays open while the page is open and closes by
    itself a quarter of an hour after it is shut.

    A suspended shop sends nothing, so its process stops either way.
    """
    rows = (db.table("businesses")
            .select("id, wa_port, agent_token, plan_expires_on, "
                    "wa_server_requested_at, wa_linked_at")
            .execute()).data or []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
        minutes=settings.wa_pair_window_min)
    out = []
    for r in rows:
        if not r.get("wa_port") or not r.get("agent_token"):
            continue
        if subs.live_status(r.get("plan_expires_on")) == "suspended":
            continue
        if r.get("wa_linked_at"):
            out.append({"business_id": r["id"], "port": int(r["wa_port"]),
                        "token": r["agent_token"]})
            continue
        asked = _parse_ts(r.get("wa_server_requested_at"))
        if asked is None or asked < cutoff:
            continue                      # nobody is waiting on this QR
        out.append({"business_id": r["id"], "port": int(r["wa_port"]), "token": r["agent_token"]})
    return out


def _parse_ts(ts):
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def touch_request(db, business_id: str) -> None:
    """Someone is on the QR screen right now: hold the pairing window open."""
    try:
        db.table("businesses").update(
            {"wa_server_requested_at": _now_iso()}).eq("id", business_id).execute()
    except Exception:
        log.debug("could not refresh the pairing window for %s", business_id, exc_info=True)


def manager_request_allowed(client_host: str, headers) -> bool:
    """Only the local manager may read the shop list (it carries agent tokens):
    a localhost source, no tunnel headers, and the WA_API_KEY."""
    key = (settings.wa_api_key or "").strip()
    if not key or client_host not in ("127.0.0.1", "::1", "localhost"):
        return False
    lower = {str(k).lower(): v for k, v in dict(headers).items()}
    if any(h in lower for h in _TUNNEL_HEADERS):
        return False
    return hmac.compare_digest(str(lower.get("x-asva-key", "")), key)
