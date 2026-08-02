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
from app import site
from app.routers import (admin, bills, businesses, clients, downloads, eod,
                         health, license, mobile, ops, tally, webhooks)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Starting ASVA (env=%s)", settings.app_env)
    if not settings.supabase_configured:
        log.warning("Supabase not configured - running in degraded/local mode.")
    if not settings.aisensy_configured:
        log.warning("AiSensy not configured - WhatsApp sends will be logged, not sent.")
    # Central error capture: swallowed exceptions land in alert_log (+ Sentry if
    # SENTRY_DSN is set), so a silent failure is still visible in the ops center.
    try:
        from app.services import errorlog
        errorlog.install()
    except Exception:
        log.warning("error capture not installed", exc_info=True)
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
app.include_router(license.router)            # /license/heartbeat - server-authoritative subscription
app.include_router(ops.router)                # /ops - operator command center (health + subscriptions)
app.include_router(mobile.router)             # /m - read-only mobile companion (PWA)
app.include_router(downloads.router)          # /download - public software download page
app.include_router(site.router)               # public marketing site: / , /how-it-works, /features, /pricing, /use-cases, sitemap, robots


@app.get("/api")
def api_root():
    return {
        "service": "asva",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/pay")
def pay_page(plan: str = "pro"):
    """A tappable renewal-pay page. The renewal WhatsApp notice links here with an
    https URL (which WhatsApp DOES make clickable, unlike a raw upi:// scheme).
    Tapping opens this page, which then opens the owner's UPI app with ASVA's UPI
    id and the exact amount prefilled - so 'renew' really does start a UPI payment.
    The UPI id is also shown as copyable text for anyone whose phone doesn't hand
    off automatically."""
    from html import escape
    from urllib.parse import quote
    from fastapi.responses import HTMLResponse
    from app.config import settings
    from app.services.subscription import _plan_price

    _plan, price = _plan_price(plan)
    upi = (settings.operator_upi_id or "").strip()
    payee = (settings.operator_upi_name or "ASVA").strip()
    if not upi:
        return HTMLResponse("<p style='font-family:sans-serif;padding:24px'>"
                            "Payment is not set up yet. Please reply to us on WhatsApp to renew.</p>")
    note = f"ASVA renewal ({_plan.value})"
    # Keep the VPA's @ and . raw - UPI apps expect the literal id in pa=.
    link = (f"upi://pay?pa={quote(upi, safe='@._-')}&pn={quote(payee)}"
            f"&am={price}&cu=INR&tn={quote(note)}")
    esc_upi, esc_link = escape(upi), escape(link, quote=True)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Renew ASVA</title><style>
 body{{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;background:#F7F6F3;color:#2F3437;margin:0;
  display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}}
 .card{{background:#fff;border:1px solid #EAEAEA;border-radius:16px;padding:28px 24px;max-width:360px;width:100%;text-align:center}}
 .amt{{font-size:2rem;font-weight:800;letter-spacing:-.02em;margin:6px 0 2px}}
 .sub{{color:#787774;font-size:.9rem;margin-bottom:20px}}
 .pay{{display:block;background:#0a7d33;color:#fff;font-weight:700;font-size:1.05rem;
  padding:14px;border-radius:12px;text-decoration:none}}
 .pay:active{{background:#086b2b}}
 .upi{{margin-top:16px;font-size:.86rem;color:#5a6b60}}
 .upi b{{user-select:all}}
</style></head><body>
 <div class="card">
   <div class="sub">Renew ASVA</div>
   <div class="amt">&#8377;{price:,}</div>
   <div class="sub">{escape(payee)} plan</div>
   <a class="pay" id="pay" href="{esc_link}">Pay with any UPI app</a>
   <div class="upi">Or pay this UPI id in your app:<br><b>{esc_upi}</b></div>
 </div>
 <script>
   // Best-effort auto-open of the UPI app; the button is the reliable fallback.
   setTimeout(function(){{ try{{ window.location.href={link!r}; }}catch(e){{}} }}, 350);
 </script>
</body></html>"""
    return HTMLResponse(html)
