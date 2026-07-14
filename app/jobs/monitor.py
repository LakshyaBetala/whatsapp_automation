"""Watchdog job - runs every few minutes on the host.

Builds the health snapshot, checks the bot WhatsApp live, turns problems into
alerts (emailing you once per incident), and stamps its own heartbeat. This is
what turns "something is broken" into a mail in your inbox within minutes.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.db import require_db
from app.services import alerts, monitoring

log = logging.getLogger(__name__)


async def _check_bot_wa() -> bool | None:
    """True = bot WhatsApp reachable + connected, False = configured but down,
    None = no bot configured (skip)."""
    base = (settings.platform_wa_url or "").strip()
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.get(base.rstrip("/") + "/api/wa/status")
            return bool(r.json().get("ready"))
    except Exception:
        return False   # configured but unreachable = down


async def run() -> None:
    db = require_db()
    try:
        health = monitoring.build_health(db)
        bot = await _check_bot_wa()
        if bot is not None:
            health.setdefault("system", {})["bot_wa"] = {"ok": bot}
        problems = monitoring.evaluate(health)
        result = alerts.reconcile(db, problems)
        monitoring.stamp_job(db, "monitor", ok=True,
                             detail=f"{len(problems)} open, {result['emailed']} emailed")
        if result["opened"] or result["resolved"]:
            log.info("Monitor: opened=%s resolved=%s emailed=%s",
                     result["opened"], result["resolved"], result["emailed"])
    except Exception:
        log.exception("Monitor watchdog failed")
        monitoring.stamp_job(db, "monitor", ok=False, detail="watchdog error")
