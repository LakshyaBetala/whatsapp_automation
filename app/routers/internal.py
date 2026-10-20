"""Internal endpoints for processes on this box only (never the public).

GET /internal/wa-shops - the shop WhatsApp processes wa_service/manager.js
should run. It carries each shop's agent token, so it answers only a localhost
caller with the WA_API_KEY and no Cloudflare-tunnel headers; anyone else gets a
plain 404, as if the route did not exist.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.db import require_db
from app.services import wa_server

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/wa-shops")
def wa_shops(request: Request):
    host = request.client.host if request.client else ""
    if not wa_server.manager_request_allowed(host, request.headers):
        raise HTTPException(status_code=404)
    return {"shops": wa_server.shops_for_manager(require_db())}
