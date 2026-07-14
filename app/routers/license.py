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
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db import require_db
from app.models import Plan
from app.services import license as lic

log = logging.getLogger(__name__)
router = APIRouter(prefix="/license", tags=["license"])


def _require_admin(admin_key: Optional[str]) -> None:
    """Gate ops actions behind ADMIN_API_KEY. While the key is unset the
    endpoint refuses outright (safe default - no accidental open renewals)."""
    configured = (settings.admin_api_key or "").strip()
    if not configured:
        raise HTTPException(status_code=503,
                            detail="Renewal is disabled: set ADMIN_API_KEY in the server .env first.")
    if not admin_key or not secrets.compare_digest(admin_key, configured):
        raise HTTPException(status_code=401, detail="Invalid admin key")


class HeartbeatPayload(BaseModel):
    agent_token: str
    machine_id: Optional[str] = None
    agent_version: Optional[str] = None
    wa_ready: Optional[bool] = None       # shop's own WhatsApp connected? (health)
    outbox_pending: Optional[int] = None  # queued sends the shop still owes


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
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    update: dict = {"last_seen": now_iso}
    if payload.agent_version:
        update["agent_version"] = payload.agent_version[:40]
    if payload.machine_id and not (biz.get("machine_id") or "").strip():
        update["machine_id"] = payload.machine_id[:120]
    if payload.wa_ready is not None:
        update["wa_ready"] = bool(payload.wa_ready)
        update["wa_checked_at"] = now_iso
    if payload.outbox_pending is not None:
        update["outbox_pending"] = max(0, int(payload.outbox_pending))
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


class CreateBizPayload(BaseModel):
    admin_key: str
    owner_name: str
    whatsapp_number: str
    business_name: Optional[str] = None
    plan: str = "starter"
    months: float = 1                 # first paid cycle length (30-day cycles)


@router.post("/create-business")
async def create_business(payload: CreateBizPayload):
    """OPS ONLY: onboard a new shop. Creates the business row and mints its
    agent_token + licence key + first paid cycle, and returns them so the
    operator can drop the token into the new shop's config. The agent_token is
    a SECRET shown once here - it is never exposed by any read endpoint."""
    _require_admin(payload.admin_key)
    db = require_db()
    try:
        biz = lic.create_business(
            db, owner_name=payload.owner_name, whatsapp_number=payload.whatsapp_number,
            business_name=payload.business_name, plan=payload.plan, months=payload.months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        log.exception("create_business failed")
        raise HTTPException(status_code=409,
                            detail="Could not create - is that WhatsApp number already used?")
    log.info("Onboarded business %s (%s)", biz.get("id"), biz.get("business_name"))
    return {
        "ok": True,
        "business_id": biz["id"],
        "business_name": biz.get("business_name") or "",
        "owner_name": biz.get("owner_name") or "",
        "whatsapp_number": biz.get("whatsapp_number") or "",
        "agent_token": biz["agent_token"],       # secret, shown once
        "license_key": biz["license_key"],
        "plan": biz["plan"],
        "plan_expires_on": str(biz.get("plan_expires_on"))[:10],
    }


class RenewPayload(BaseModel):
    admin_key: str
    # Identify the business by ONE of these (agent_token is easiest from config).
    agent_token: Optional[str] = None
    license_key: Optional[str] = None
    business_id: Optional[str] = None
    months: float = 1                 # 30-day cycles to add (fractions ok)
    plan: Optional[str] = None        # optionally move the plan tier


@router.post("/renew")
async def renew(payload: RenewPayload):
    """OPS ONLY: mark a business paid - extend its 30-day cycle (and optionally
    set its plan). The client can NEVER call this; it needs ADMIN_API_KEY.

    Renewing on time stacks onto the remaining days; renewing late starts from
    today. Returns the fresh authoritative state."""
    _require_admin(payload.admin_key)
    db = require_db()

    sel = "id, business_name, plan, plan_expires_on, license_key, machine_id, agent_version"
    q = db.table("businesses").select(sel)
    if payload.agent_token:
        q = q.eq("agent_token", payload.agent_token)
    elif payload.license_key:
        q = q.eq("license_key", payload.license_key)
    elif payload.business_id:
        q = q.eq("id", payload.business_id)
    else:
        raise HTTPException(status_code=400, detail="Give agent_token, license_key or business_id")
    r = q.order("created_at").limit(1).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Business not found")
    biz = r.data[0]

    if payload.months <= 0 or payload.months > 60:
        raise HTTPException(status_code=400, detail="months must be between 0 and 60")

    update: dict = {}
    if payload.plan:
        try:
            update["plan"] = Plan(payload.plan).value
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"Unknown plan '{payload.plan}'. Use starter/growth/pro/max.")
    new_expiry = lic.renew_expiry(biz.get("plan_expires_on"), payload.months)
    update["plan_expires_on"] = new_expiry.isoformat()
    db.table("businesses").update(update).eq("id", biz["id"]).execute()

    biz.update(update)
    hb = lic.build_heartbeat(db, biz)
    log.info("Renewed %s -> expires %s (plan %s)", biz["id"], new_expiry, hb["plan"])
    return {"ok": True, "renewed_until": new_expiry.isoformat(), "heartbeat": hb}


class SetPlanPayload(BaseModel):
    admin_key: str
    business_id: str
    plan: str


@router.post("/set-plan")
async def set_plan(payload: SetPlanPayload):
    """OPS ONLY: change a business's plan tier WITHOUT touching its expiry."""
    _require_admin(payload.admin_key)
    db = require_db()
    try:
        plan = Plan(payload.plan).value
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="Unknown plan. Use starter/growth/pro/max.")
    r = db.table("businesses").update({"plan": plan}).eq("id", payload.business_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Business not found")
    return {"ok": True, "plan": plan}


class SuspendPayload(BaseModel):
    admin_key: str
    business_id: str


@router.post("/suspend")
async def suspend(payload: SuspendPayload):
    """OPS ONLY: cut a business off now (non-payment) by expiring it past the
    grace window. Sends stop immediately (server-side). Reversible with /renew."""
    _require_admin(payload.admin_key)
    db = require_db()
    r = (db.table("businesses").select("id, plan, plan_expires_on")
         .eq("id", payload.business_id).limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=404, detail="Business not found")
    # Backdate past the grace window so effective_status = suspended right away.
    past = (_dt.date.today() - _dt.timedelta(days=lic.subs.GRACE_DAYS + 1)).isoformat()
    db.table("businesses").update({"plan_expires_on": past}).eq("id", payload.business_id).execute()
    log.info("Suspended %s (expiry set to %s)", payload.business_id, past)
    return {"ok": True, "suspended": True, "expiry": past}
