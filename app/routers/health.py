"""Liveness + readiness. The keep-alive ping also hits /health."""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.db import get_client

router = APIRouter(tags=["ops"])


@router.get("/health")
def health():
    db_ok = False
    if get_client() is not None:
        try:
            # cheap round-trip; head=True avoids pulling rows
            get_client().table("businesses").select("id", count="exact").limit(
                1
            ).execute()
            db_ok = True
        except Exception:  # noqa: BLE001 — health must never raise
            db_ok = False

    return {
        "status": "ok",
        "env": settings.app_env,
        "supabase_configured": settings.supabase_configured,
        "db_reachable": db_ok,
        "aisensy_configured": settings.aisensy_configured,
    }
