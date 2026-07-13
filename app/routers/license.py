"""License / heartbeat API - the server's authoritative subscription answer.

The client (Tally agent + desktop app) calls POST /license/heartbeat every
~30 min with its agent_token (and, optionally, its machine id + build version).
The server records that it is alive, then returns the one true subscription
state the client must obey. Enforcement of paid actions still happens
server-side on every send; this endpoint powers the client's UI + update
nudges + the ops health monitor.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import require_db
from app.services import license as lic

log = logging.getLogger(__name__)
router = APIRouter(prefix="/license", tags=["license"])


class HeartbeatPayload(BaseModel):
    agent_token: str
    machine_id: Optional[str] = None
    agent_version: Optional[str] = None


_BIZ_COLS = ("id, business_name, plan, plan_expires_on, license_key, "
             "machine_id, agent_version")


def _biz_by_token(db, token: str) -> dict:
    r = (db.table("businesses").select(_BIZ_COLS)
         .eq("agent_token", token).order("created_at").limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return r.data[0]


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload):
    """Record the client as alive and return the authoritative subscription
    state (plan, expiry, remaining messages, debtor cap, feature flags, update
    info). Safe to call often; it is cheap and idempotent."""
    db = require_db()
    biz = _biz_by_token(db, payload.agent_token)

    # Record liveness for the health monitor (best-effort - never fail the
    # heartbeat over a bookkeeping write). machine_id is set once and only
    # changed if it was empty, so a copied install shows a different id later.
    update: dict = {"last_seen": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    if payload.agent_version:
        update["agent_version"] = payload.agent_version[:40]
    if payload.machine_id and not (biz.get("machine_id") or "").strip():
        update["machine_id"] = payload.machine_id[:120]
    try:
        db.table("businesses").update(update).eq("id", biz["id"]).execute()
    except Exception:
        log.exception("heartbeat liveness write failed (continuing)")

    return lic.build_heartbeat(db, biz)


@router.get("/status")
async def status(token: str):
    """Read-only status for a dashboard/browser (no liveness write). Same body
    as the heartbeat, keyed by the agent token as a query param."""
    db = require_db()
    biz = _biz_by_token(db, token)
    return lic.build_heartbeat(db, biz)
