"""Deliver the queue of shops whose WhatsApp runs on ASVA's server.

For such a shop the server plays the part a laptop plays for the others: it
pulls the shop's queued sends (outbox.pull, caller="server": same send window,
same 72-hour expiry, same promise re-check), sends each through the shop's own
WhatsApp process on 127.0.0.1:<port> (wa_service/index.js, started by
manager.js) with the same random human-like gap, and acks the result
(outbox.ack: same audit, same cadence-marker settling, same PDF cleanup).

Extra ban safety a laptop never had: at most settings.wa_server_hourly_cap
sends per number per hour. Shops are drained concurrently (each is its own
number); one shop's slow queue never holds up another.

Also keeps businesses.wa_ready fresh for server shops, and flips a shop to
wa_transport='server' the first time its process reports a linked WhatsApp
(covers an owner who scanned the QR and closed the page).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import random

import httpx

from app.config import settings
from app.db import get_client
from app.jobs.outbox_sweep import MAX_HARD_ATTEMPTS
from app.services import outbox, wa_server

log = logging.getLogger(__name__)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


async def _send(port: int, payload: dict) -> tuple[str, str | None]:
    """('sent' | 'transient' | 'failed', error). Transient = the shop's WhatsApp
    is not reachable or not linked right now: the queue waits."""
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(f"{wa_server.child_url(port)}/api/wa/send",
                                json=payload, headers=wa_server._headers())
        if r.status_code == 503:
            return "transient", "WhatsApp not ready"
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code == 200 and data.get("success", True):
            return "sent", None
        return "failed", str(data.get("error") or f"HTTP {r.status_code}")[:300]
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        return "transient", repr(e)[:300]
    except Exception as e:
        return "failed", repr(e)[:300]


def _sent_last_hour(db, business_id: str) -> int:
    since = (_now() - _dt.timedelta(hours=1)).isoformat()
    try:
        r = (db.table("wa_outbox").select("id", count="exact")
             .eq("business_id", business_id).eq("status", "sent")
             .gte("sent_at", since).execute())
        return int(r.count or 0)
    except Exception:
        return 0


async def _drain(db, shop: dict) -> None:
    bid, port = shop["id"], shop["wa_port"]
    st = await wa_server.status(port)
    ready = bool(st and st.get("ready"))
    if ready and shop.get("wa_transport") != "server":
        wa_server.mark_connected(db, bid, already_linked=bool(shop.get("wa_linked_at")))
        shop["wa_transport"] = "server"
    if shop.get("wa_transport") != "server":
        return                      # still being linked; the laptop keeps sending
    try:
        db.table("businesses").update(
            {"wa_ready": ready, "wa_checked_at": _now().isoformat()}).eq("id", bid).execute()
    except Exception:
        log.debug("wa_ready update failed for %s", bid, exc_info=True)
    if not ready:
        return
    budget = settings.wa_server_hourly_cap - _sent_last_hour(db, bid)
    if budget <= 0:
        return
    items = outbox.pull(db, bid, limit=min(budget, outbox.PULL_LIMIT), caller="server")
    for i, item in enumerate(items):
        if i:
            await asyncio.sleep(random.uniform(settings.send_gap_min_s, settings.send_gap_max_s))
        result, err = await _send(port, item["payload"])
        attempts = int(item.get("attempts") or 0) + 1
        if result == "sent":
            outbox.ack(db, bid, item["id"], "sent", attempts)
        elif result == "transient":
            outbox.ack(db, bid, item["id"], "queued", attempts, err)
            break                   # WhatsApp dropped mid-run: the rest waits
        else:
            final = ("failed" if err == "not_on_whatsapp" or attempts >= MAX_HARD_ATTEMPTS
                     else "queued")
            outbox.ack(db, bid, item["id"], final, attempts, err)


async def run() -> None:
    db = get_client()
    if db is None:
        return
    try:
        rows = (db.table("businesses")
                .select("id, wa_port, wa_transport, wa_linked_at, wa_server_requested_at")
                .execute()).data or []
    except Exception:
        log.debug("server outbox: businesses read failed (migration 056?)", exc_info=True)
        return
    shops = [r for r in rows if r.get("wa_port") and r.get("wa_server_requested_at")]
    if not shops:
        return
    results = await asyncio.gather(*(_drain(db, s) for s in shops), return_exceptions=True)
    for s, res in zip(shops, results):
        if isinstance(res, Exception):
            log.error("server outbox failed for %s: %r", s.get("id"), res)
