"""FastAPI entry point.

Boots the app, starts the in-process scheduler on startup, and wires routers.
The app boots even without Supabase/AiSensy keys so you can iterate locally;
DB-backed endpoints will report the missing configuration clearly.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import scheduler
from app.config import settings
from app.routers import admin, bills, businesses, clients, eod, health, tally, webhooks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Starting ASVA (env=%s)", settings.app_env)
    if not settings.supabase_configured:
        log.warning("Supabase not configured — running in degraded/local mode.")
    if not settings.aisensy_configured:
        log.warning("AiSensy not configured — WhatsApp sends will be logged, not sent.")
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        log.info("Shutdown complete.")


app = FastAPI(
    title="ASVA",
    version="0.2.0",
    summary="Automatic WhatsApp bills, reminders and EOD digest from TallyPrime.",
    lifespan=lifespan,
)

# Router order matters for /docs readability
app.include_router(health.router)
app.include_router(businesses.router, prefix="/businesses")
app.include_router(clients.router, prefix="/clients")
app.include_router(bills.router, prefix="/bills")
app.include_router(tally.router)          # already has prefix="/tally"
app.include_router(webhooks.router)       # already has prefix="/webhooks"
app.include_router(eod.router, prefix="/eod")
app.include_router(admin.router)              # /admin tick-box page (LAN)


@app.get("/")
def root():
    return {
        "service": "asva",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }
