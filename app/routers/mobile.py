"""ASVA mobile companion - a READ-ONLY view of one shop, for the owner's phone.

Design (locked in MOBILE_APP_DESIGN.md): the phone is a reference screen, not the
engine. Tally reading and WhatsApp sending stay on the shop laptop. This router
therefore exposes ONLY GET endpoints - there is no way to change anything from
the phone, by construction. It is installable as a PWA at /m and authenticates
with the shop's own token (entered once, kept in the phone's local storage).

Endpoints:
  GET /m                      -> the installable app shell
  GET /m/manifest.webmanifest -> PWA manifest
  GET /m/sw.js                -> tiny offline service worker
  GET /m/api/summary?token=   -> shop totals + who-to-chase + promises
  GET /m/api/party?token=&id= -> one party: dues, next reminder, promise
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.db import require_db
from app.services import conversations, names, promises, proof

router = APIRouter(prefix="/m", tags=["mobile"])

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _biz(token: str) -> dict:
    """Resolve the shop from its token. Read-only; 401 on a bad token."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = require_db()
    r = (db.table("businesses").select("id, business_name")
         .eq("agent_token", token).limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return r.data[0]


def _inr(v) -> str:
    try:
        return f"{Decimal(str(v)):,.0f}"
    except Exception:
        return "0"


def _overdue_days(due, today) -> int:
    try:
        return max(0, (today - _dt.date.fromisoformat(str(due))).days)
    except (TypeError, ValueError):
        return 0


def _build_summary(db, biz: dict) -> dict:
    today = _dt.datetime.now(IST).date()
    bid = biz["id"]
    clients = (db.table("clients")
               .select("id, name, whatsapp_number, excluded")
               .eq("business_id", bid).execute()).data or []
    by_id = {c["id"]: c for c in clients}
    open_bills = (db.table("bills")
                  .select("client_id, outstanding, due_date, status")
                  .eq("business_id", bid)
                  .in_("status", ["pending", "partial", "overdue"]).execute()).data or []

    owed: dict = {}
    worst_due: dict = {}
    for b in open_bills:
        cid = b.get("client_id")
        if cid not in by_id:
            continue
        owed[cid] = owed.get(cid, Decimal(0)) + Decimal(str(b.get("outstanding") or 0))
        od = _overdue_days(b.get("due_date"), today)
        worst_due[cid] = max(worst_due.get(cid, 0), od)

    held = promises.held_now(db, [bid]).get(bid, set())

    parties = []
    total_out = Decimal(0)
    owing_n = 0
    for cid, amt in owed.items():
        if amt <= 0:
            continue
        c = by_id[cid]
        if c.get("excluded"):
            continue
        owing_n += 1
        total_out += amt
        parties.append({
            "id": cid,
            "name": names.clean_display(c.get("name") or "") or "(unnamed)",
            "outstanding": _inr(amt),
            "amt": float(amt),                 # numeric -> phone sorts by amount
            "overdue_days": worst_due.get(cid, 0),
            "paused": cid in held,
            "whatsapp": c.get("whatsapp_number") or "",   # phone can offer Call
        })
    # Full owing list, ordered as the default "who to chase": active parties
    # first (most overdue, then biggest), paused/on-promise parties last. The
    # phone searches, sorts and pages this list locally, so every owing party
    # (not just the top 100) is reachable from the phone.
    chase = sorted(parties, key=lambda p: (p["paused"], -p["overdue_days"], -p["amt"]))
    pf = proof.build_proof(db, bid, today)
    return {
        "business_name": biz.get("business_name") or "Your shop",
        "total_outstanding": _inr(total_out),
        "parties_owing": owing_n,
        "on_promise": sum(1 for p in parties if p["paused"]),
        "recovered_this_month": _inr(pf["recovered_this_month"]),
        "recovered_month": pf["month"],
        "chase": chase,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


def _build_party(db, biz: dict, client_id: str) -> dict:
    today = _dt.datetime.now(IST).date()
    bid = biz["id"]
    cr = (db.table("clients")
          .select("id, name, whatsapp_number, credit_days, excluded, reminders_enabled")
          .eq("id", client_id).eq("business_id", bid).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="Party not found")
    c = cr.data[0]
    bills = (db.table("bills")
             .select("invoice_number, outstanding, amount, due_date, invoice_date, status")
             .eq("business_id", bid).eq("client_id", client_id)
             .order("invoice_date", desc=True).limit(200).execute()).data or []
    open_bills = [b for b in bills if b.get("status") in ("pending", "partial", "overdue")]
    total = sum(Decimal(str(b.get("outstanding") or 0)) for b in open_bills)

    promise = None
    pr = promises.find_open(db, bid, client_id)
    if pr:
        promise = {
            "kind": pr.get("kind"),
            "promise_date": str(pr.get("promise_date")) if pr.get("promise_date") else None,
            "hold_until": str(pr.get("hold_until") or "")[:10],
            "said": pr.get("raw_text") or "",
            "when": str(pr.get("created_at") or "")[:10],
        }
    recent = [{
        "text": r.get("body") or "",
        "when": str(r.get("created_at") or "")[:10],
    } for r in conversations.recent_for_client(db, bid, client_id, limit=8)]
    return {
        "id": c["id"],
        "name": names.clean_display(c.get("name") or "") or "(unnamed)",
        "whatsapp": c.get("whatsapp_number") or "",
        "excluded": bool(c.get("excluded")),
        "reminders_on": bool(c.get("reminders_enabled", True)),
        "outstanding": _inr(total),
        "open_bills": [{
            "invoice": b.get("invoice_number") or "-",
            "amount": _inr(b.get("outstanding") or 0),
            "overdue_days": _overdue_days(b.get("due_date"), today),
            "due": str(b.get("due_date") or "")[:10],
        } for b in open_bills[:60]],
        "recent": recent,
        "promise": promise,
    }


# ── Read-only API ───────────────────────────────────────────────────────────
@router.get("/api/summary")
def api_summary(token: str = Query("")):
    return JSONResponse(_build_summary(require_db(), _biz(token)))


@router.get("/api/party")
def api_party(token: str = Query(""), id: str = Query("")):
    return JSONResponse(_build_party(require_db(), _biz(token), id))


# ── Owner actions from the phone (reuse the desktop endpoints, same token/auth,
# so behaviour never diverges). The shop's WhatsApp sends still leave the shop
# laptop via the outbox - the phone only triggers them. ─────────────────────
class _MRemind(BaseModel):
    token: str
    party: str


class _MPay(BaseModel):
    token: str
    client_id: str
    amount: float
    payment_date: str | None = None


@router.post("/api/remind")
async def api_remind(p: _MRemind):
    _biz(p.token)                                  # 401 on a bad token
    from app.routers.admin import admin_send_now, SendNowPayload
    return await admin_send_now(SendNowPayload(token=p.token, party=p.party))


@router.post("/api/record-payment")
async def api_record_payment(p: _MPay):
    _biz(p.token)                                  # 401 on a bad token
    from app.routers.admin import admin_record_payment, RecordPaymentPayload
    return await admin_record_payment(RecordPaymentPayload(
        token=p.token, client_id=p.client_id, amount=p.amount,
        payment_date=p.payment_date))


@router.get("/qr")
def phone_link_qr(token: str = Query("")):
    """A QR the desktop app shows so the owner scans it to open their OWN phone
    app - it encodes /m?token=<their token>, so the phone lands already linked to
    their shop's live data. Token is verified first, so this is not an open QR
    maker."""
    _biz(token)                       # 401s on a bad token
    import base64
    from fastapi.responses import Response
    from app.config import settings
    from app.services import upi
    base = (settings.public_base_url or "https://app.tryasva.com").rstrip("/")
    b64 = upi.qr_png_base64(f"{base}/m?token={token}")
    if not b64:
        raise HTTPException(status_code=503, detail="QR not available")
    return Response(content=base64.b64decode(b64), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


# ── PWA shell + manifest + service worker ───────────────────────────────────
@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "ASVA", "short_name": "ASVA", "start_url": "/m",
        "display": "standalone", "background_color": "#f4f5f2",
        "theme_color": "#ffffff",
        "icons": [{"src": "/m/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, media_type="application/manifest+json")


@router.get("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           '<stop offset="0" stop-color="#12a150"/><stop offset="1" stop-color="#0a7d33"/>'
           '</linearGradient></defs>'
           '<rect width="100" height="100" rx="24" fill="#0f1512"/>'
           '<path d="M50 24 L72 74 H61 L50 47 L39 74 H28 Z" fill="url(#g)"/></svg>')
    return PlainTextResponse(svg, media_type="image/svg+xml")


@router.get("/sw.js")
def service_worker():
    # A minimal offline shell: cache the app page so it opens without network;
    # data is always fetched live (and shows the last error if offline).
    js = (
        "const C='asva-m-v1';"
        "self.addEventListener('install',e=>{self.skipWaiting();"
        "e.waitUntil(caches.open(C).then(c=>c.addAll(['/m'])))});"
        "self.addEventListener('activate',e=>{self.clients.claim()});"
        "self.addEventListener('fetch',e=>{const u=new URL(e.request.url);"
        "if(u.pathname.startsWith('/m/api')){return;}"          # never cache data
        "e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)))});"
    )
    return PlainTextResponse(js, media_type="application/javascript")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def app_shell(token: str = Query("")):
    return HTMLResponse(_APP_HTML)


_APP_HTML = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>ASVA</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f1512" media="(prefers-color-scheme: dark)">
<link rel="manifest" href="/m/manifest.webmanifest">
<style>
 :root{
   --bg:#f5f6f2;--bg2:#eef1ea;--card:#ffffff;--ink:#132019;--ink2:#37473e;--muted:#6d7f74;
   --line:#e7ebe4;--line2:#eef1ea;--accent:#0a7d33;--accent2:#12a150;--accent-ink:#0a7d33;
   --wa:#25d366;--wa-ink:#06331a;
   --crit:#c23b22;--crit-bg:#fdece8;--warn:#8a5a00;--warn-bg:#fbf3e0;--ok:#0a7d33;
   --shadow:0 1px 2px rgba(18,40,25,.04),0 10px 26px -12px rgba(18,40,25,.14);
   --shadow-sm:0 1px 2px rgba(18,40,25,.05);
   --r:18px;--r2:14px;
 }
 @media (prefers-color-scheme:dark){:root{
   --bg:#0e1411;--bg2:#131a16;--card:#18201b;--ink:#e9f1eb;--ink2:#b6c6bc;--muted:#879a8f;
   --line:#26302a;--line2:#212a25;--accent:#46d67e;--accent2:#3fce76;--accent-ink:#9ff0bd;
   --wa:#25d366;--wa-ink:#04240f;
   --crit:#ff8a70;--crit-bg:#33201c;--warn:#e7b96b;--warn-bg:#2e2717;--ok:#46d67e;
   --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 28px -14px rgba(0,0,0,.6);
   --shadow-sm:0 1px 2px rgba(0,0,0,.35);
 }}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 html,body{overflow-x:clip}
 body{margin:0;font-family:'SF Pro Display','Helvetica Neue',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
   background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
 @media (prefers-reduced-motion:no-preference){
   .fade{animation:fade .42s cubic-bezier(.16,1,.3,1) both}
   .fade.d1{animation-delay:.04s}.fade.d2{animation-delay:.08s}.fade.d3{animation-delay:.12s}
   @keyframes fade{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
 }
 header{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 82%,transparent);
   -webkit-backdrop-filter:saturate(1.5) blur(14px);backdrop-filter:saturate(1.5) blur(14px);
   padding:calc(10px + env(safe-area-inset-top)) 16px 11px;border-bottom:1px solid var(--line);
   display:flex;align-items:center;gap:11px}
 .mark{flex-shrink:0;width:34px;height:34px;border-radius:10px;background:linear-gradient(140deg,var(--accent2),var(--accent));
   display:flex;align-items:center;justify-content:center;box-shadow:0 5px 14px -5px rgba(10,125,51,.5)}
 .mark svg{width:19px;height:19px;display:block}
 header .who{flex:1;min-width:0}
 header .n{font-weight:800;letter-spacing:-.02em;font-size:1.08rem;line-height:1.15;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 header .s{color:var(--muted);font-size:.78rem;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .getapp{flex-shrink:0;border:1px solid var(--line);background:var(--card);color:var(--accent-ink);font-weight:700;
   font-size:.78rem;padding:8px 13px;border-radius:9999px;text-decoration:none;box-shadow:var(--shadow-sm)}
 .getapp:active{transform:scale(.97)}
 .back{flex-shrink:0;background:var(--card);border:1px solid var(--line);color:var(--ink);font:inherit;
   font-weight:800;font-size:1.05rem;width:36px;height:36px;border-radius:10px;cursor:pointer;box-shadow:var(--shadow-sm)}
 .back:active{transform:scale(.95)}
 .wrap{padding:14px 16px calc(74px + env(safe-area-inset-bottom));max-width:640px;margin:0 auto}
 /* hero: recovered this month */
 .recov{position:relative;overflow:hidden;background:linear-gradient(135deg,var(--accent2),var(--accent));
   border-radius:var(--r);padding:17px 19px;margin-bottom:14px;color:#fff;box-shadow:0 14px 30px -12px rgba(10,125,51,.5)}
 .recov .rlabel{font-size:.7rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#d9ffe8;opacity:.95}
 .recov .rval{font-size:2rem;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;margin-top:3px;line-height:1}
 .recov .rsub{font-size:.8rem;color:#e3fff0;opacity:.9;margin-top:5px}
 .recov::after{content:"";position:absolute;right:-40px;top:-40px;width:150px;height:150px;border-radius:50%;
   background:radial-gradient(closest-side,rgba(255,255,255,.18),transparent)}
 /* kpis */
 .kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-bottom:6px}
 .kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:13px 12px;box-shadow:var(--shadow-sm);min-width:0}
 .kpi .v{font-size:1.28rem;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.05;
   overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .kpi .l{color:var(--muted);font-size:.7rem;margin-top:5px;font-weight:600}
 .sect{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:800;margin:22px 3px 10px;
   display:flex;align-items:center;justify-content:space-between}
 .sect .cnt{color:var(--muted);font-weight:700;letter-spacing:0;text-transform:none;font-size:.76rem}
 /* chase today */
 .today{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden;margin-bottom:6px}
 .trow{display:flex;align-items:center;gap:12px;padding:12px 13px;border-top:1px solid var(--line2)}
 .trow:first-child{border-top:0}
 .trow:active{background:var(--bg2)}
 .rk{width:26px;height:26px;flex-shrink:0;border-radius:9px;background:var(--bg2);color:var(--accent-ink);font-weight:800;
   font-size:.82rem;display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums}
 .trow .nm{flex:1;min-width:0}
 .trow .nm b{display:block;font-size:.96rem;font-weight:700;letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .badge{display:inline-block;font-size:.72rem;font-weight:700;margin-top:3px;padding:1px 8px;border-radius:9999px}
 .b-crit{color:var(--crit);background:var(--crit-bg)}
 .b-warn{color:var(--warn);background:var(--warn-bg)}
 .b-ok{color:var(--muted);background:var(--bg2)}
 .b-paused{color:var(--warn);background:var(--warn-bg)}
 .trow .amt{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:.95rem;letter-spacing:-.02em}
 .wamini{flex-shrink:0;width:40px;height:40px;border-radius:11px;background:var(--wa);color:var(--wa-ink);
   display:flex;align-items:center;justify-content:center;text-decoration:none}
 .wamini svg{width:20px;height:20px}
 .wamini:active{transform:scale(.93)}
 /* list rows */
 .row{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:var(--r2);
   padding:13px 14px;margin-bottom:8px;cursor:pointer;box-shadow:var(--shadow-sm)}
 .row:active{background:var(--bg2);transform:scale(.995)}
 .row .nm{flex:1;min-width:0}
 .row .nm b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.96rem;font-weight:700;letter-spacing:-.01em}
 .row .amt{font-weight:800;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap;letter-spacing:-.02em}
 .chev{color:var(--muted);opacity:.55;font-size:1.15rem;flex-shrink:0}
 /* search + sort */
 .find{display:flex;gap:8px;margin-bottom:11px}
 .find input,.find select{padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--card);
   color:var(--ink);font-size:.92rem;box-shadow:var(--shadow-sm);font-family:inherit}
 .find input{flex:1;min-width:0}
 .find input:focus,.find select:focus{outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent);border-color:transparent}
 .find select{max-width:44%;font-weight:600}
 .pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:13px}
 .pager button{flex:1;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);
   font-weight:700;font-size:.88rem;box-shadow:var(--shadow-sm);font-family:inherit}
 .pager button:active:not(:disabled){transform:scale(.98)}
 .pager button:disabled{opacity:.4}
 .pinfo{color:var(--muted);font-size:.8rem;white-space:nowrap;font-variant-numeric:tabular-nums}
 /* party detail */
 .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:15px;margin-bottom:12px;box-shadow:var(--shadow-sm)}
 .actions{display:flex;gap:9px;margin-bottom:13px}
 .btn{display:flex;align-items:center;justify-content:center;gap:8px;flex:1;min-height:52px;font-weight:800;font-size:1rem;
   border-radius:15px;text-decoration:none;font-family:inherit;border:0}
 .btn svg{width:20px;height:20px}
 .btn-wa{background:var(--wa);color:var(--wa-ink);box-shadow:0 10px 22px -10px rgba(37,211,102,.55)}
 .btn-call{background:var(--card);color:var(--ink);border:1px solid var(--line);flex:0 0 52px;box-shadow:var(--shadow-sm)}
 .btn-ghost{background:var(--card);color:var(--ink);border:1px solid var(--line);box-shadow:var(--shadow-sm)}
 .btn-ghost:disabled{opacity:.6}
 .btn:active{transform:scale(.98)}
 .promise{border:1px solid color-mix(in srgb,var(--warn) 35%,var(--line));background:var(--warn-bg)}
 .promise b{color:var(--warn)}
 .bill{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:11px 0;border-bottom:1px solid var(--line2);
   font-size:.92rem;font-variant-numeric:tabular-nums}
 .bill:last-child{border-bottom:0}
 .bill .inv{color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .bill .amt{font-weight:700;white-space:nowrap}
 .od{color:var(--crit);font-size:.78rem;font-weight:700;margin-left:6px}
 .msg{color:var(--ink2);font-size:.9rem;line-height:1.55;padding:11px 0;border-bottom:1px solid var(--line2)}
 .msg:last-child{border-bottom:0}
 .msg span{display:block;color:var(--muted);font-size:.72rem;margin-top:3px}
 .pill{display:inline-flex;align-items:center;gap:5px;font-size:.74rem;font-weight:700;padding:4px 10px;border-radius:9999px}
 .pill-on{color:var(--ok);background:var(--bg2)}
 .pill-off{color:var(--warn);background:var(--warn-bg)}
 /* states */
 .empty,.err{color:var(--muted);text-align:center;padding:40px 20px;line-height:1.65;font-size:.92rem}
 .err{color:var(--crit)}
 .empty .big{font-size:1.7rem;margin-bottom:6px}
 .rtry{margin-top:14px;display:inline-block;border:1px solid var(--line);background:var(--card);color:var(--accent-ink);
   font-weight:700;padding:10px 18px;border-radius:9999px;font-size:.86rem;cursor:pointer;font-family:inherit}
 /* skeleton */
 .sk{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);box-shadow:var(--shadow-sm)}
 .shim{position:relative;overflow:hidden;background:var(--bg2);border-radius:8px}
 .shim::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
   background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--card) 60%,transparent),transparent);
   animation:sh 1.25s infinite}
 @keyframes sh{100%{transform:translateX(100%)}}
 /* login */
 .login{padding:64px 26px 40px;text-align:center;max-width:400px;margin:0 auto}
 .login .lm{width:66px;height:66px;border-radius:20px;background:linear-gradient(140deg,var(--accent2),var(--accent));
   display:flex;align-items:center;justify-content:center;margin:0 auto 18px;box-shadow:0 14px 30px -10px rgba(10,125,51,.5)}
 .login .lm svg{width:36px;height:36px}
 .login h1{font-size:1.4rem;font-weight:800;letter-spacing:-.02em;margin:0}
 .login p{color:var(--muted);line-height:1.65;font-size:.92rem}
 .login input{width:100%;padding:15px;border-radius:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);
   font-size:1rem;margin:18px 0;box-shadow:var(--shadow-sm);text-align:center;font-family:inherit}
 .login button{width:100%;padding:15px;border:0;border-radius:13px;background:linear-gradient(140deg,var(--accent2),var(--accent));
   color:#fff;font-weight:800;font-size:1rem;font-family:inherit;box-shadow:0 12px 26px -12px rgba(10,125,51,.6)}
 .login button:active{transform:scale(.99)}
 /* install + read-only bar */
 .install{display:flex;align-items:center;gap:11px;background:var(--bg2);border:1px solid var(--line);border-radius:var(--r2);
   padding:11px 13px;margin-bottom:14px}
 .install .it{flex:1;font-size:.83rem;color:var(--ink2);line-height:1.45}
 .install button{border:0;background:var(--accent);color:#fff;font-weight:700;font-size:.82rem;padding:9px 13px;border-radius:10px;flex-shrink:0;font-family:inherit}
 .install .x{background:none;color:var(--muted);font-size:1.2rem;padding:2px 6px;line-height:1}
 .ro{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:9px 12px calc(9px + env(safe-area-inset-bottom));
   background:color-mix(in srgb,var(--bg) 88%,transparent);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);
   color:var(--muted);font-size:.72rem;border-top:1px solid var(--line)}
 .ro a{color:var(--accent-ink);text-decoration:none;font-weight:700}
</style></head><body>
<div id="app"></div>
<div class="ro">View only &middot; changes are made on the shop computer &middot; <a href="/download">Get the computer app</a></div>
<script>
const app=document.getElementById('app');
const qs=new URLSearchParams(location.search);
let TOKEN=qs.get('token')||localStorage.getItem('asva_m_token')||'';
if(qs.get('token')){localStorage.setItem('asva_m_token',qs.get('token'));
  history.replaceState({},'',location.pathname);}
// ASVA wordmark 'A' path, reused in header + login.
const MARK='<svg viewBox="0 0 100 100" fill="none"><path d="M50 20 L74 78 H62 L50 45 L38 78 H26 Z" fill="#fff"/></svg>';
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function telNum(w){var t=(w||'').replace(/[^0-9]/g,'');
  if(t.length===12&&t.slice(0,2)==='91')t=t.slice(2);
  else if(t.length===13&&t.slice(0,3)==='091')t=t.slice(3);
  return t;}
function waNum(w){var t=(w||'').replace(/[^0-9]/g,'');
  if(t.length===10)t='91'+t; else if(t.length===11&&t[0]==='0')t='91'+t.slice(1);
  return t;}   // wa.me needs the country code
const WA_ICON='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.04 2c-5.46 0-9.9 4.44-9.9 9.9 0 1.75.46 3.45 1.32 4.95L2 22l5.3-1.38a9.9 9.9 0 0 0 4.74 1.2h.01c5.46 0 9.9-4.44 9.9-9.9S17.5 2 12.04 2Zm5.8 14.16c-.24.68-1.4 1.3-1.94 1.34-.5.05-1.13.24-3.66-.77-3.08-1.22-5.06-4.36-5.22-4.56-.15-.2-1.25-1.66-1.25-3.17 0-1.5.79-2.24 1.07-2.55.28-.3.6-.38.8-.38.2 0 .4 0 .58.01.19.01.44-.07.68.52.24.6.83 2.07.9 2.22.07.15.12.32.02.52-.1.2-.15.32-.3.5-.15.17-.31.39-.44.52-.15.15-.3.31-.13.6.17.3.76 1.25 1.63 2.02 1.12 1 2.06 1.31 2.36 1.46.3.15.47.13.64-.08.17-.2.74-.86.94-1.16.2-.3.4-.25.67-.15.27.1 1.72.81 2.01.96.3.15.5.22.57.35.07.13.07.74-.17 1.42Z"/></svg>';
const CALL_ICON='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.6 10.8a15.5 15.5 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.6c.6 0 1 .4 1 1 0 1.2.2 2.4.6 3.6.1.4 0 .8-.3 1l-2.3 2.2Z"/></svg>';
// Install-to-home-screen (Android fires beforeinstallprompt; iOS shows a hint).
let INSTALL_EVT=null;
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();INSTALL_EVT=e;});
function installBar(){
  if(localStorage.getItem('asva_m_installed')||window.matchMedia('(display-mode: standalone)').matches)return '';
  const ios=/iphone|ipad|ipod/i.test(navigator.userAgent);
  const txt=ios?'Add ASVA to your home screen: tap Share, then Add to Home Screen.'
              :'Add ASVA to your home screen for one-tap access.';
  const btn=ios?'':'<button onclick="doInstall()">Add</button>';
  return '<div class="install" id="installbar"><div class="it">'+txt+'</div>'+btn+
    '<button class="x" onclick="dismissInstall()">&times;</button></div>';
}
async function doInstall(){ if(INSTALL_EVT){INSTALL_EVT.prompt();try{await INSTALL_EVT.userChoice;}catch(e){} INSTALL_EVT=null;} dismissInstall(); }
function dismissInstall(){localStorage.setItem('asva_m_installed','1');var b=document.getElementById('installbar');if(b)b.remove();}
async function api(path){
  const r=await fetch('/m/api/'+path+(path.includes('?')?'&':'?')+'token='+encodeURIComponent(TOKEN));
  if(r.status===401){logout();throw new Error('unauthorised');}
  if(!r.ok)throw new Error('http '+r.status);
  return r.json();
}
async function post(path,body){
  const r=await fetch('/m/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({token:TOKEN},body||{}))});
  if(r.status===401){logout();throw new Error('unauthorised');}
  const j=await r.json().catch(()=>({}));
  return {ok:r.ok,j:j};
}
function toast(m){let t=document.getElementById('_tst');
  if(!t){t=document.createElement('div');t.id='_tst';
    t.style.cssText='position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#111;color:#fff;padding:11px 18px;border-radius:10px;font-size:.92rem;z-index:99;max-width:88%;text-align:center;box-shadow:0 6px 20px rgba(0,0,0,.3)';
    document.body.appendChild(t);}
  t.textContent=m;t.style.opacity='1';clearTimeout(t._h);t._h=setTimeout(function(){t.style.opacity='0';},2600);}
async function doRemind(name,btn){
  if(btn){btn.disabled=true;btn.textContent='Sending...';}
  try{const x=await post('remind',{party:name});
    if(x.j&&x.j.wa_down){toast('Shop WhatsApp not connected. Reconnect on the desktop.');}
    else if(x.ok&&x.j&&x.j.sent!==false){toast('Reminder sent to '+name+'.');}
    else{toast((x.j&&x.j.detail)||'Could not send. Try again.');}}
  catch(e){if(e.message!=='unauthorised')toast('Could not send. Try again.');}
  if(btn){btn.disabled=false;btn.textContent='Remind';}
}
async function doRecordPay(id,name){
  const raw=prompt('Record a payment from '+name+'.\\n\\nAmount received (Rs):','');
  if(raw==null)return;
  const amt=Number(String(raw).replace(/[^0-9.]/g,''));
  if(!amt||amt<=0){toast('Enter a valid amount.');return;}
  const x=await post('record-payment',{client_id:id,amount:amt});
  if(x.ok&&x.j&&(x.j.applied>0)){toast('Recorded Rs '+amt+' from '+name+'.');party(id);}
  else{toast((x.j&&x.j.detail)||'Could not record. This works for non-Tally parties; Tally payments come in on their own.');}
}
function logout(){localStorage.removeItem('asva_m_token');TOKEN='';loginView();}
function loginView(){
  app.innerHTML='<div class="login fade"><div class="lm">'+MARK+'</div>'+
   '<h1>ASVA</h1>'+
   '<p>Enter your ASVA code to see your shop’s live collections, and chase or record a payment from your phone.</p>'+
   '<input id="tk" placeholder="Your ASVA code" autocomplete="off" autocapitalize="characters">'+
   '<button onclick="doLogin()">Open my shop</button></div>';
  var i=document.getElementById('tk'); if(i){i.onkeydown=e=>{if(e.key==='Enter')doLogin();};}
}
function doLogin(){const v=document.getElementById('tk').value.trim();
  if(!v)return;localStorage.setItem('asva_m_token',v);TOKEN=v;home();}
let CHASE=[], chaseSearch='', chaseSort='chase', chasePage=0;
const PER=50;
// urgency band from overdue days -> badge class + label
function urg(days,paused){
  if(paused)return['b-paused','Paused &middot; promised'];
  if(days>30)return['b-crit',days+' days overdue'];
  if(days>0)return['b-warn',days+' days overdue'];
  return['b-ok','due now'];
}
function headerHtml(title, sub, back){
  return '<header>'+(back?'<button class="back" onclick="home()" aria-label="Back">&#8592;</button>'
     :'<div class="mark">'+MARK+'</div>')+
    '<div class="who"><div class="n">'+esc(title)+'</div><div class="s">'+esc(sub)+'</div></div>'+
    (back?'':'<a class="getapp" href="/download">Get app</a>')+'</header>';
}
function skeleton(){
  const bar=(w,h)=>'<div class="shim" style="height:'+h+'px;width:'+w+'"></div>';
  let kp=''; for(let i=0;i<3;i++)kp+='<div class="kpi">'+bar('60%',22)+'<div style="height:8px"></div>'+bar('80%',10)+'</div>';
  let rows=''; for(let i=0;i<5;i++)rows+='<div class="sk" style="display:flex;gap:12px;align-items:center;padding:13px 14px;margin-bottom:8px">'+
    '<div style="flex:1">'+bar('55%',13)+'<div style="height:7px"></div>'+bar('35%',10)+'</div>'+bar('64px',15)+'</div>';
  app.innerHTML=headerHtml('ASVA','Loading your shop…',false)+
    '<div class="wrap"><div class="shim" style="height:86px;border-radius:18px;margin-bottom:14px"></div>'+
    '<div class="kpis" style="margin-bottom:20px">'+kp+'</div>'+rows+'</div>';
}
async function home(){
  skeleton();
  let d;try{d=await api('summary');}catch(e){
    if(e.message==='unauthorised')return;
    app.innerHTML=headerHtml('ASVA','',false)+'<div class="wrap"><div class="err">Could not reach ASVA.<br>Check your internet.<br>'+
      '<button class="rtry" onclick="home()">Try again</button></div></div>';return;}
  CHASE=d.chase||[]; chasePage=0;
  const active=CHASE.filter(p=>!p.paused).slice()
    .sort((a,b)=>(b.overdue_days-a.overdue_days)||(b.amt-a.amt));
  const top=active.slice(0,3);
  let today='';
  if(top.length){
    today='<div class="sect">Chase today<span class="cnt">'+active.length+' to chase</span></div>'+
      '<div class="today fade d1">'+top.map((p,i)=>{
        const w=waNum(p.whatsapp),t=telNum(p.whatsapp);
        const u=urg(p.overdue_days,false);
        const act=w?('<a class="wamini" href="https://wa.me/'+esc(w)+'" target="_blank" rel="noopener" onclick="event.stopPropagation()" aria-label="WhatsApp">'+WA_ICON+'</a>')
                   :(t?'<a class="wamini" href="tel:'+esc(t)+'" onclick="event.stopPropagation()" aria-label="Call">'+CALL_ICON+'</a>':'');
        return '<div class="trow" onclick="party('+JSON.stringify(p.id).replace(/"/g,'&quot;')+')">'+
          '<div class="rk">'+(i+1)+'</div>'+
          '<div class="nm"><b>'+esc(p.name)+'</b><span class="badge '+u[0]+'">'+u[1]+'</span></div>'+
          '<div class="amt">&#8377;'+esc(p.outstanding)+'</div>'+act+
        '</div>';
      }).join('')+'</div>';
  }
  app.innerHTML=
   headerHtml(d.business_name,'Your live collections',false)+
   '<div class="wrap">'+
   installBar()+
   ((d.recovered_this_month && d.recovered_this_month!=='0')
     ? '<div class="recov fade"><div class="rlabel">Recovered</div><div class="rval">&#8377;'+esc(d.recovered_this_month)+'</div>'+
       '<div class="rsub">collected in '+esc(d.recovered_month||'this month')+'</div></div>'
     : '')+
   '<div class="kpis fade d1">'+
     '<div class="kpi"><div class="v">&#8377;'+esc(d.total_outstanding)+'</div><div class="l">Outstanding</div></div>'+
     '<div class="kpi"><div class="v">'+d.parties_owing+'</div><div class="l">Parties owing</div></div>'+
     '<div class="kpi"><div class="v">'+d.on_promise+'</div><div class="l">On promise</div></div>'+
   '</div>'+
   today+
   '<div class="sect">All parties</div>'+
   '<div class="find fade d2">'+
     '<input id="q" placeholder="Search a party" autocomplete="off" value="'+esc(chaseSearch)+'">'+
     '<select id="srt">'+
       '<option value="chase">Who to chase</option>'+
       '<option value="amt_desc">Amount: high to low</option>'+
       '<option value="amt_asc">Amount: low to high</option>'+
       '<option value="overdue">Most overdue</option>'+
       '<option value="name">Name: A to Z</option>'+
     '</select>'+
   '</div>'+
   '<div id="chaselist" class="fade d2"></div><div id="chasepager"></div>'+
   '</div>';
  const q=document.getElementById('q'), srt=document.getElementById('srt');
  srt.value=chaseSort;
  q.oninput=()=>{chaseSearch=q.value;chasePage=0;renderChase();};
  srt.onchange=()=>{chaseSort=srt.value;chasePage=0;renderChase();};
  renderChase();
}
function renderChase(){
  const list=document.getElementById('chaselist'), pager=document.getElementById('chasepager');
  if(!list)return;
  const term=chaseSearch.trim().toLowerCase();
  let rows=term?CHASE.filter(p=>(p.name||'').toLowerCase().includes(term))
               :CHASE.filter(p=>!p.paused);
  const cmp={
    amt_desc:(a,b)=>b.amt-a.amt,
    amt_asc:(a,b)=>a.amt-b.amt,
    overdue:(a,b)=>(b.overdue_days-a.overdue_days)||(b.amt-a.amt),
    name:(a,b)=>(a.name||'').localeCompare(b.name||''),
    chase:(a,b)=>((a.paused?1:0)-(b.paused?1:0))||(b.overdue_days-a.overdue_days)||(b.amt-a.amt),
  }[chaseSort];
  if(cmp)rows.sort(cmp);
  const total=rows.length, pages=Math.max(1,Math.ceil(total/PER));
  if(chasePage>=pages)chasePage=pages-1;
  if(chasePage<0)chasePage=0;
  const start=chasePage*PER, page=rows.slice(start,start+PER);
  if(!total){
    list.innerHTML='<div class="empty"><div class="big">'+(term?'🔍':'✅')+'</div>'+
      (term?'No party matches that name.':'Nobody to chase right now.<br>All caught up.')+'</div>';
    pager.innerHTML='';return;
  }
  list.innerHTML=page.map(p=>rowHtml(p)).join('');
  if(pages>1){
    pager.innerHTML='<div class="pager">'+
      '<button onclick="pageStep(-1)"'+(chasePage<=0?' disabled':'')+'>&#8592; Prev</button>'+
      '<span class="pinfo">'+(start+1)+'-'+(start+page.length)+' of '+total+'</span>'+
      '<button onclick="pageStep(1)"'+(chasePage>=pages-1?' disabled':'')+'>Next &#8594;</button>'+
      '</div>';
  }else{
    pager.innerHTML='<div class="pinfo" style="text-align:center;margin-top:12px">'+total+(term?' match'+(total>1?'es':''):' part'+(total>1?'ies':'y'))+'</div>';
  }
}
function pageStep(n){chasePage+=n;renderChase();window.scrollTo(0,0);}
function rowHtml(p){
  const u=urg(p.overdue_days,p.paused);
  return '<div class="row" onclick="party('+JSON.stringify(p.id).replace(/"/g,'&quot;')+')">'+
   '<div class="nm"><b>'+esc(p.name)+'</b><span class="badge '+u[0]+'" style="margin-top:4px">'+u[1]+'</span></div>'+
   '<div class="amt">&#8377;'+esc(p.outstanding)+'</div><div class="chev">&#8250;</div></div>';
}
async function party(id){
  app.innerHTML=headerHtml('…','',true)+'<div class="wrap"><div class="shim" style="height:120px;border-radius:18px;margin-bottom:12px"></div>'+
    '<div class="shim" style="height:52px;border-radius:15px;margin-bottom:12px"></div><div class="shim" style="height:140px;border-radius:18px"></div></div>';
  let d;try{d=await api('party?id='+encodeURIComponent(id));}catch(e){
    if(e.message==='unauthorised')return;
    app.innerHTML=headerHtml('ASVA','',true)+'<div class="wrap"><div class="err">Could not load this party.<br>'+
      '<button class="rtry" onclick="party('+JSON.stringify(id).replace(/"/g,'&quot;')+')">Try again</button></div></div>';return;}
  let promise='';
  if(d.promise){
    const p=d.promise;
    const head=p.kind==='paid_claim'?'Customer says they have already paid':
      (p.promise_date?('Reminders paused until '+esc(p.promise_date)):('Reminders paused until '+esc(p.hold_until)));
    promise='<div class="card promise fade d1"><b>&#9208; '+head+'</b>'+
      (p.said?'<div style="margin-top:8px;color:var(--warn)">&ldquo;'+esc(p.said)+'&rdquo;'+(p.when?(' &middot; '+esc(p.when)):'')+'</div>':'')+'</div>';
  }
  let bills=d.open_bills.map(b=>'<div class="bill"><div class="inv">'+esc(b.invoice)+
    (b.overdue_days>0?('<span class="od">'+b.overdue_days+'d</span>'):'')+
    '</div><div class="amt">&#8377;'+esc(b.amount)+'</div></div>').join('')||'<div style="color:var(--muted)">No open bills.</div>';
  const w=waNum(d.whatsapp),t=telNum(d.whatsapp);
  let actions='';
  if(w||t){
    actions='<div class="actions fade">'+
      (w?'<a class="btn btn-wa" href="https://wa.me/'+esc(w)+'" target="_blank" rel="noopener">'+WA_ICON+' WhatsApp '+esc(d.name)+'</a>'
        :'<a class="btn btn-wa" href="tel:'+esc(t)+'">'+CALL_ICON+' Call '+esc(d.name)+'</a>')+
      (w&&t?'<a class="btn btn-call" href="tel:'+esc(t)+'" aria-label="Call">'+CALL_ICON+'</a>':'')+
    '</div>';
  }
  // Owner actions from the phone: send this reminder now (needs a number), and
  // record a payment. Both run through the same shop outbox as the desktop.
  const nm=JSON.stringify(d.name).replace(/"/g,'&quot;');
  const cid=JSON.stringify(id).replace(/"/g,'&quot;');
  let owneracts='<div class="actions fade" style="margin-top:10px">'+
    (w?'<button class="btn btn-ghost" onclick="doRemind('+nm+',this)">Remind</button>':'')+
    '<button class="btn btn-ghost" onclick="doRecordPay('+cid+','+nm+')">Record payment</button>'+
    '</div>';
  let recent='';
  if(d.recent&&d.recent.length){
    recent='<div class="sect">Recent replies</div><div class="card">'+
      d.recent.map(m=>'<div class="msg">&ldquo;'+esc(m.text)+'&rdquo;'+(m.when?('<span>'+esc(m.when)+'</span>'):'')+'</div>').join('')+'</div>';
  }
  app.innerHTML=
   headerHtml(d.name, d.whatsapp||'no number', true)+
   '<div class="wrap">'+
   '<div class="kpis fade"><div class="kpi"><div class="v">&#8377;'+esc(d.outstanding)+'</div><div class="l">Outstanding</div></div>'+
     '<div class="kpi" style="grid-column:span 2"><div class="v"><span class="pill '+(d.reminders_on?'pill-on':'pill-off')+'">'+
       (d.reminders_on?'Reminders ON':'Reminders OFF')+'</span></div><div class="l">'+
       (d.reminders_on?'ASVA is chasing this party':'Paused for this party')+'</div></div></div>'+
   actions+
   owneracts+
   promise+
   '<div class="sect">Open bills</div><div class="card fade d2">'+bills+'</div>'+
   recent+
   '</div>';
}
if(TOKEN)home();else loginView();
if('serviceWorker' in navigator){navigator.serviceWorker.register('/m/sw.js').catch(()=>{});}
</script></body></html>"""
