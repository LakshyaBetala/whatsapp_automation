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
            "_amt": float(amt),
            "overdue_days": worst_due.get(cid, 0),
            "paused": cid in held,
        })
    # Who to chase = owing, not paused, most overdue first.
    chase = sorted([p for p in parties if not p["paused"]],
                   key=lambda p: (-p["overdue_days"], -p["_amt"]))
    for p in parties:
        p.pop("_amt", None)
    pf = proof.build_proof(db, bid, today)
    return {
        "business_name": biz.get("business_name") or "Your shop",
        "total_outstanding": _inr(total_out),
        "parties_owing": owing_n,
        "on_promise": sum(1 for p in parties if p["paused"]),
        "recovered_this_month": _inr(pf["recovered_this_month"]),
        "recovered_month": pf["month"],
        "chase": chase[:100],
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
        "display": "standalone", "background_color": "#0f1512",
        "theme_color": "#17211b",
        "icons": [{"src": "/m/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, media_type="application/manifest+json")


@router.get("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<rect width="100" height="100" rx="22" fill="#17211b"/>'
           '<text x="50" y="66" font-size="52" font-family="Arial" font-weight="bold" '
           'fill="#46d67e" text-anchor="middle">A</text></svg>')
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
<meta name="theme-color" content="#17211b">
<link rel="manifest" href="/m/manifest.webmanifest">
<style>
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 body{margin:0;font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;background:#0f1512;color:#eef3ef}
 header{position:sticky;top:0;background:#17211b;padding:14px 16px calc(14px + env(safe-area-inset-top)) 16px;border-bottom:1px solid #24332b}
 header .n{font-weight:800;letter-spacing:.03em;font-size:1.15rem}
 header .s{color:#9fb7a6;font-size:.82rem;margin-top:2px}
 .wrap{padding:14px 16px 40px}
 .recov{background:linear-gradient(135deg,#1c5a40,#123a2a);border:1px solid #2c6a4c;border-radius:14px;padding:14px 16px;margin-bottom:12px;font-size:1.5rem;font-weight:800;color:#eafff2}
 .recov span{display:block;font-size:.72rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#8fd6b0;margin-top:2px}
 .kpis{display:flex;gap:10px;margin-bottom:16px}
 .kpi{flex:1;background:#16221b;border:1px solid #24332b;border-radius:14px;padding:14px}
 .kpi .v{font-size:1.35rem;font-weight:800}
 .kpi .l{color:#9fb7a6;font-size:.74rem;margin-top:3px}
 .sect{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:#7f978a;margin:18px 2px 8px}
 .row{display:flex;align-items:center;gap:12px;background:#16221b;border:1px solid #24332b;border-radius:12px;padding:13px 14px;margin-bottom:8px;cursor:pointer}
 .row:active{background:#1e2c23}
 .row .nm{flex:1;min-width:0}
 .row .nm b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .row .nm span{color:#9fb7a6;font-size:.8rem}
 .row .amt{font-weight:800;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
 .paused{color:#e0b34a}
 .empty,.err{color:#9fb7a6;text-align:center;padding:36px 16px;line-height:1.6}
 .err{color:#e2916a}
 .back{background:none;border:0;color:#8fdca9;font:inherit;font-size:.95rem;padding:6px 0;cursor:pointer}
 .card{background:#16221b;border:1px solid #24332b;border-radius:14px;padding:15px;margin-bottom:12px}
 .promise{border-left:4px solid #c9a227;background:#241f10}
 .promise b{color:#f0d68a}
 .bill{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #24332b;font-size:.92rem}
 .bill:last-child{border-bottom:0}
 .od{color:#e2916a;font-size:.82rem}
 .callbtn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
   min-height:52px;background:#25d366;color:#0a2e17;font-weight:800;font-size:1.02rem;
   border-radius:14px;text-decoration:none;margin-bottom:12px}
 .callbtn:active{filter:brightness(.94)}
 .msg{color:#cdd9cf;font-size:.9rem;line-height:1.5;padding:9px 0;border-bottom:1px solid #24332b}
 .msg:last-child{border-bottom:0}
 .msg span{display:block;color:#7f978a;font-size:.72rem;margin-top:3px}
 .login{padding:40px 22px;text-align:center}
 .login input{width:100%;padding:13px;border-radius:10px;border:1px solid #2c4536;background:#16221b;color:#eef3ef;font-size:1rem;margin:14px 0}
 .login button{width:100%;padding:13px;border:0;border-radius:10px;background:#46d67e;color:#10321f;font-weight:800;font-size:1rem}
 .ro{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:8px calc(8px + env(safe-area-inset-bottom));background:#0f1512;color:#5f7568;font-size:.72rem;border-top:1px solid #1c261f}
</style></head><body>
<div id="app"></div>
<div class="ro">View only. Changes are made on the shop computer.</div>
<script>
const app=document.getElementById('app');
const qs=new URLSearchParams(location.search);
let TOKEN=qs.get('token')||localStorage.getItem('asva_m_token')||'';
if(qs.get('token')){localStorage.setItem('asva_m_token',qs.get('token'));
  history.replaceState({},'',location.pathname);}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
async function api(path){
  const r=await fetch('/m/api/'+path+(path.includes('?')?'&':'?')+'token='+encodeURIComponent(TOKEN));
  if(r.status===401){logout();throw new Error('unauthorised');}
  if(!r.ok)throw new Error('http '+r.status);
  return r.json();
}
function logout(){localStorage.removeItem('asva_m_token');TOKEN='';loginView();}
function loginView(){
  app.innerHTML='<div class="login"><div style="font-size:1.4rem;font-weight:800;letter-spacing:.04em">ASVA</div>'+
   '<p style="color:#9fb7a6;line-height:1.6">Enter your ASVA code to see your shop. This is a view-only screen.</p>'+
   '<input id="tk" placeholder="Your ASVA code" autocomplete="off">'+
   '<button onclick="doLogin()">Open my shop</button></div>';
}
function doLogin(){const v=document.getElementById('tk').value.trim();
  if(!v)return;localStorage.setItem('asva_m_token',v);TOKEN=v;home();}
async function home(){
  app.innerHTML='<div class="empty">Loading...</div>';
  let d;try{d=await api('summary');}catch(e){
    if(e.message==='unauthorised')return;
    app.innerHTML='<div class="err">Could not reach ASVA. Check the internet and pull to refresh.</div>';return;}
  let chase=d.chase.map(p=>rowHtml(p)).join('')||'<div class="empty">Nobody to chase right now. All caught up.</div>';
  app.innerHTML=
   '<header><div class="n">'+esc(d.business_name)+'</div><div class="s">Tap a party to see details</div></header>'+
   '<div class="wrap">'+
   ((d.recovered_this_month && d.recovered_this_month!=='0')
     ? '<div class="recov">&#8377;'+esc(d.recovered_this_month)+' <span>recovered in '+esc(d.recovered_month||'')+'</span></div>'
     : '')+
   '<div class="kpis">'+
     '<div class="kpi"><div class="v">&#8377;'+esc(d.total_outstanding)+'</div><div class="l">Outstanding</div></div>'+
     '<div class="kpi"><div class="v">'+d.parties_owing+'</div><div class="l">Parties owing</div></div>'+
     '<div class="kpi"><div class="v">'+d.on_promise+'</div><div class="l">On promise</div></div>'+
   '</div>'+
   '<div class="sect">Who to chase</div>'+chase+
   '</div>';
}
function rowHtml(p){
  const od=p.overdue_days>0?('<span>'+p.overdue_days+' days overdue</span>'):(p.paused?'<span class="paused">Paused &middot; promised</span>':'<span>not overdue</span>');
  return '<div class="row" onclick="party('+JSON.stringify(p.id).replace(/"/g,'&quot;')+')">'+
   '<div class="nm"><b>'+esc(p.name)+'</b>'+od+'</div>'+
   '<div class="amt">&#8377;'+esc(p.outstanding)+'</div></div>';
}
async function party(id){
  app.innerHTML='<div class="empty">Loading...</div>';
  let d;try{d=await api('party?id='+encodeURIComponent(id));}catch(e){
    if(e.message==='unauthorised')return;
    app.innerHTML='<div class="err">Could not load this party. Pull to refresh.</div>';return;}
  let promise='';
  if(d.promise){
    const p=d.promise;
    const head=p.kind==='paid_claim'?'Customer says they have already paid':
      (p.promise_date?('Reminders paused until '+esc(p.promise_date)):('Reminders paused until '+esc(p.hold_until)));
    promise='<div class="card promise"><b>&#9208; '+head+'</b>'+
      (p.said?'<div style="margin-top:8px;color:#d8c58a">&ldquo;'+esc(p.said)+'&rdquo;'+(p.when?(' &middot; '+esc(p.when)):'')+'</div>':'')+'</div>';
  }
  let bills=d.open_bills.map(b=>'<div class="bill"><div>'+esc(b.invoice)+
    (b.overdue_days>0?(' <span class="od">'+b.overdue_days+'d</span>'):'')+
    '</div><div>&#8377;'+esc(b.amount)+'</div></div>').join('')||'<div style="color:#9fb7a6">No open bills.</div>';
  const tel=(d.whatsapp||'').replace(/[^0-9+]/g,'');
  const call=tel?('<a class="callbtn" href="tel:'+esc(tel)+'">&#128222; Call '+esc(d.name)+'</a>'):'';
  let recent='';
  if(d.recent&&d.recent.length){
    recent='<div class="sect">Recent replies</div><div class="card">'+
      d.recent.map(m=>'<div class="msg">&ldquo;'+esc(m.text)+'&rdquo;'+(m.when?('<span>'+esc(m.when)+'</span>'):'')+'</div>').join('')+'</div>';
  }
  app.innerHTML=
   '<header><button class="back" onclick="home()">&#8592; Back</button>'+
     '<div class="n">'+esc(d.name)+'</div><div class="s">'+esc(d.whatsapp||'no number')+'</div></header>'+
   '<div class="wrap">'+
   '<div class="kpis"><div class="kpi"><div class="v">&#8377;'+esc(d.outstanding)+'</div><div class="l">Outstanding</div></div>'+
     '<div class="kpi"><div class="v">'+(d.reminders_on?'ON':'OFF')+'</div><div class="l">Reminders</div></div></div>'+
   call+
   promise+
   '<div class="sect">Open bills</div><div class="card">'+bills+'</div>'+
   recent+
   '</div>';
}
if(TOKEN)home();else loginView();
if('serviceWorker' in navigator){navigator.serviceWorker.register('/m/sw.js').catch(()=>{});}
</script></body></html>"""
