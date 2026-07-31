"""Liveness (/health) + readiness (/ready).

/health is a PURE liveness check: it returns instantly and never touches the
database, so a slow or briefly-paused Supabase can never make it time out. That
matters because an uptime monitor watches /health every few minutes on a
single-worker server - a blocking DB call here was flagging the whole service
"down" whenever Supabase hiccuped, even though the app was fine.

/ready is the deep check (liveness + a cheap DB round-trip). Call it deliberately
when you actually want to know the database is reachable - not from a tight
uptime monitor.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.db import get_client

router = APIRouter(tags=["ops"])


@router.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Is the web process up and answering? No DB call - never blocks."""
    return {
        "status": "ok",
        "version": settings.app_version,
        "env": settings.app_env,
    }


@router.get("/ready")
def ready():
    """Liveness + a cheap database round-trip. Use for a deliberate deep check."""
    db_ok = False
    if get_client() is not None:
        try:
            get_client().table("businesses").select("id", count="exact").limit(1).execute()
            db_ok = True
        except Exception:  # noqa: BLE001 - readiness must never raise
            db_ok = False
    return {
        "status": "ok",
        "version": settings.app_version,
        "env": settings.app_env,
        "supabase_configured": settings.supabase_configured,
        "db_reachable": db_ok,
        "aisensy_configured": settings.aisensy_configured,
    }
