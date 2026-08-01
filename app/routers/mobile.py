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
<meta name="theme-color" content="#ffffff">
<link rel="manifest" href="/m/manifest.webmanifest">
<style>
 :root{--bg:#f4f5f2;--card:#ffffff;--ink:#16211b;--muted:#6b7d72;--line:#e7eae5;
   --green:#0a7d33;--greenink:#0a7d33;--wa:#25d366;--amber:#9a6a00;--shadow:0 1px 2px rgba(20,40,25,.05),0 8px 24px rgba(20,40,25,.05)}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 body{margin:0;font-family:'SF Pro Display','Helvetica Neue',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
 header{position:sticky;top:0;z-index:5;background:rgba(255,255,255,.86);backdrop-filter:saturate(1.4) blur(10px);
   padding:12px 16px calc(12px + env(safe-area-inset-top)) 16px;border-bottom:1px solid var(--line);
   display:flex;align-items:center;gap:12px}
 header .who{flex:1;min-width:0}
 header .n{font-weight:800;letter-spacing:-.01em;font-size:1.1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 header .s{color:var(--muted);font-size:.8rem;margin-top:1px}
 .getapp{flex-shrink:0;border:1px solid var(--line);background:#fff;color:var(--green);font-weight:700;
   font-size:.8rem;padding:8px 12px;border-radius:9999px;text-decoration:none;box-shadow:var(--shadow)}
 .getapp:active{background:#f0f3ef}
 .wrap{padding:14px 16px 92px;max-width:640px;margin:0 auto}
 .recov{background:linear-gradient(135deg,#0f9d58,#0a7d33);border-radius:18px;padding:16px 18px;margin-bottom:14px;
   font-size:1.6rem;font-weight:800;color:#fff;box-shadow:0 10px 24px rgba(10,125,51,.22)}
 .recov span{display:block;font-size:.72rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#d6ffe6;margin-top:2px}
 .kpis{display:flex;gap:10px;margin-bottom:16px}
 .kpi{flex:1;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 12px;box-shadow:var(--shadow)}
 .kpi .v{font-size:1.3rem;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
 .kpi .l{color:var(--muted);font-size:.72rem;margin-top:4px}
 .sect{font-size:.74rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:20px 2px 9px}
 .today{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:6px 6px 4px;margin-bottom:6px;box-shadow:var(--shadow);overflow:hidden}
 .today .th{display:flex;align-items:baseline;justify-content:space-between;padding:12px 12px 6px}
 .today .th b{font-size:.98rem}
 .today .th span{color:var(--muted);font-size:.75rem}
 .trow{display:flex;align-items:center;gap:12px;padding:11px 12px;border-top:1px solid var(--line)}
 .trow:first-of-type{border-top:0}
 .trow .rk{width:22px;height:22px;flex-shrink:0;border-radius:50%;background:#eef3ee;color:var(--green);font-weight:800;
   font-size:.8rem;display:flex;align-items:center;justify-content:center}
 .trow .nm{flex:1;min-width:0}
 .trow .nm b{display:block;font-size:.95rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .trow .nm span{color:var(--amber);font-size:.76rem}
 .trow .amt{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:.92rem}
 .callmini{flex-shrink:0;width:38px;height:38px;border-radius:50%;background:var(--wa);color:#0a2e17;
   display:flex;align-items:center;justify-content:center;text-decoration:none;font-size:1.05rem}
 .callmini:active{filter:brightness(.94)}
 .row{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;
   padding:13px 14px;margin-bottom:8px;cursor:pointer;box-shadow:var(--shadow)}
 .row:active{background:#f6f8f5}
 .row .nm{flex:1;min-width:0}
 .row .nm b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.96rem}
 .row .nm span{color:var(--muted);font-size:.78rem}
 .row .amt{font-weight:800;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
 .paused{color:var(--amber)}
 .chev{color:#c3ccc5;font-size:1.1rem;flex-shrink:0}
 .empty,.err{color:var(--muted);text-align:center;padding:34px 16px;line-height:1.6}
 .err{color:#b5482f}
 .find{display:flex;gap:8px;margin-bottom:10px}
 .find input{flex:1;min-width:0;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:.95rem;box-shadow:var(--shadow)}
 .find input:focus{outline:2px solid #bfe6cd;border-color:#bfe6cd}
 .find select{padding:12px 10px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:.82rem;max-width:44%;box-shadow:var(--shadow)}
 .pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:12px}
 .pager button{flex:1;padding:12px;border:1px solid var(--line);border-radius:12px;background:#fff;color:var(--ink);font-weight:700;font-size:.9rem;box-shadow:var(--shadow)}
 .pager button:disabled{opacity:.4}
 .pager .pinfo{color:var(--muted);font-size:.8rem;white-space:nowrap}
 .back{background:none;border:0;color:var(--green);font:inherit;font-weight:700;font-size:.95rem;padding:6px 0;cursor:pointer}
 .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:12px;box-shadow:var(--shadow)}
 .promise{border-left:4px solid #d8a400;background:#fffaf0}
 .promise b{color:#8a6300}
 .bill{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:.92rem;font-variant-numeric:tabular-nums}
 .bill:last-child{border-bottom:0}
 .od{color:#b5482f;font-size:.82rem}
 .callbtn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
   min-height:54px;background:var(--wa);color:#0a2e17;font-weight:800;font-size:1.02rem;
   border-radius:16px;text-decoration:none;margin-bottom:12px;box-shadow:0 8px 20px rgba(37,211,102,.2)}
 .callbtn:active{filter:brightness(.94)}
 .msg{color:#3a4a41;font-size:.9rem;line-height:1.5;padding:10px 0;border-bottom:1px solid var(--line)}
 .msg:last-child{border-bottom:0}
 .msg span{display:block;color:var(--muted);font-size:.72rem;margin-top:3px}
 .login{padding:56px 24px;text-align:center;max-width:420px;margin:0 auto}
 .logo{width:64px;height:64px;border-radius:18px;background:linear-gradient(135deg,#0f9d58,#0a7d33);color:#fff;
   font-weight:800;font-size:2rem;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;box-shadow:0 10px 24px rgba(10,125,51,.25)}
 .login input{width:100%;padding:14px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:1rem;margin:16px 0;box-shadow:var(--shadow)}
 .login button{width:100%;padding:14px;border:0;border-radius:12px;background:var(--green);color:#fff;font-weight:800;font-size:1rem}
 .install{display:flex;align-items:center;gap:10px;background:#eef4ef;border:1px solid #d8e6dc;border-radius:14px;padding:11px 13px;margin-bottom:14px}
 .install .it{flex:1;font-size:.84rem;color:#2f4a3a;line-height:1.4}
 .install button{border:0;background:var(--green);color:#fff;font-weight:700;font-size:.82rem;padding:8px 12px;border-radius:9px;flex-shrink:0}
 .install .x{background:none;color:#7c948a;font-size:1.1rem;padding:4px 6px}
 .ro{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:9px calc(9px + env(safe-area-inset-bottom));
   background:rgba(255,255,255,.9);backdrop-filter:blur(8px);color:var(--muted);font-size:.72rem;border-top:1px solid var(--line)}
 .ro a{color:var(--green);text-decoration:none;font-weight:700}
</style></head><body>
<div id="app"></div>
<div class="ro">View only &middot; changes are made on the shop computer &middot; <a href="/download">Get the computer app</a></div>
<script>
const app=document.getElementById('app');
const qs=new URLSearchParams(location.search);
let TOKEN=qs.get('token')||localStorage.getItem('asva_m_token')||'';
if(qs.get('token')){localStorage.setItem('asva_m_token',qs.get('token'));
  history.replaceState({},'',location.pathname);}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function telNum(w){var t=(w||'').replace(/[^0-9]/g,'');
  if(t.length===12&&t.slice(0,2)==='91')t=t.slice(2);
  else if(t.length===13&&t.slice(0,3)==='091')t=t.slice(3);
  return t;}
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
function logout(){localStorage.removeItem('asva_m_token');TOKEN='';loginView();}
function loginView(){
  app.innerHTML='<div class="login"><div class="logo">A</div>'+
   '<div style="font-size:1.3rem;font-weight:800;letter-spacing:-.01em">ASVA</div>'+
   '<p style="color:var(--muted);line-height:1.6">Enter your ASVA code to see your shop. This is a view-only screen.</p>'+
   '<input id="tk" placeholder="Your ASVA code" autocomplete="off">'+
   '<button onclick="doLogin()">Open my shop</button></div>';
}
function doLogin(){const v=document.getElementById('tk').value.trim();
  if(!v)return;localStorage.setItem('asva_m_token',v);TOKEN=v;home();}
let CHASE=[], chaseSearch='', chaseSort='chase', chasePage=0;
const PER=50;
function headerHtml(title, sub, back){
  return '<header>'+(back?'<button class="back" onclick="home()">&#8592;</button>':'')+
    '<div class="who"><div class="n">'+esc(title)+'</div><div class="s">'+esc(sub)+'</div></div>'+
    (back?'':'<a class="getapp" href="/download">Get app</a>')+'</header>';
}
async function home(){
  app.innerHTML='<div class="empty">Loading...</div>';
  let d;try{d=await api('summary');}catch(e){
    if(e.message==='unauthorised')return;
    app.innerHTML='<div class="err">Could not reach ASVA. Check the internet and pull to refresh.</div>';return;}
  CHASE=d.chase||[]; chasePage=0;
  // Chase today = the 3 most urgent active parties, each with a one-tap Call.
  const active=CHASE.filter(p=>!p.paused).slice()
    .sort((a,b)=>(b.overdue_days-a.overdue_days)||(b.amt-a.amt));
  const top=active.slice(0,3);
  let today='';
  if(top.length){
    today='<div class="today"><div class="th"><b>Chase today</b><span>'+active.length+' to chase</span></div>'+
      top.map((p,i)=>{
        const t=telNum(p.whatsapp);
        const od=p.overdue_days>0?(p.overdue_days+' days overdue'):'due now';
        return '<div class="trow"><div class="rk">'+(i+1)+'</div>'+
          '<div class="nm" onclick="party('+JSON.stringify(p.id).replace(/"/g,'&quot;')+')"><b>'+esc(p.name)+'</b><span>'+od+'</span></div>'+
          '<div class="amt">&#8377;'+esc(p.outstanding)+'</div>'+
          (t?'<a class="callmini" href="tel:'+esc(t)+'" title="Call">&#128222;</a>':'')+
        '</div>';
      }).join('')+'</div>';
  }
  app.innerHTML=
   headerHtml(d.business_name,'Your live collections',false)+
   '<div class="wrap">'+
   installBar()+
   ((d.recovered_this_month && d.recovered_this_month!=='0')
     ? '<div class="recov">&#8377;'+esc(d.recovered_this_month)+' <span>recovered in '+esc(d.recovered_month||'')+'</span></div>'
     : '')+
   '<div class="kpis">'+
     '<div class="kpi"><div class="v">&#8377;'+esc(d.total_outstanding)+'</div><div class="l">Outstanding</div></div>'+
     '<div class="kpi"><div class="v">'+d.parties_owing+'</div><div class="l">Parties owing</div></div>'+
     '<div class="kpi"><div class="v">'+d.on_promise+'</div><div class="l">On promise</div></div>'+
   '</div>'+
   today+
   '<div class="sect">All parties</div>'+
   '<div class="find">'+
     '<input id="q" placeholder="Search a party by name" autocomplete="off" value="'+esc(chaseSearch)+'">'+
     '<select id="srt">'+
       '<option value="chase">Who to chase</option>'+
       '<option value="amt_desc">Amount: high to low</option>'+
       '<option value="amt_asc">Amount: low to high</option>'+
       '<option value="overdue">Most overdue</option>'+
       '<option value="name">Name: A to Z</option>'+
     '</select>'+
   '</div>'+
   '<div id="chaselist"></div><div id="chasepager"></div>'+
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
  // Default view = who to chase (active parties). Searching reaches EVERY owing
  // party, including those paused on a promise, so any name can be opened.
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
    list.innerHTML='<div class="empty">'+(term?'No party matches that name.':'Nobody to chase right now. All caught up.')+'</div>';
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
    pager.innerHTML='<div class="pinfo" style="text-align:center;margin-top:10px">'+total+(term?' match'+(total>1?'es':''):' part'+(total>1?'ies':'y'))+'</div>';
  }
}
function pageStep(n){chasePage+=n;renderChase();window.scrollTo(0,0);}
function rowHtml(p){
  const od=p.overdue_days>0?('<span>'+p.overdue_days+' days overdue</span>'):(p.paused?'<span class="paused">Paused &middot; promised</span>':'<span>not overdue</span>');
  return '<div class="row" onclick="party('+JSON.stringify(p.id).replace(/"/g,'&quot;')+')">'+
   '<div class="nm"><b>'+esc(p.name)+'</b>'+od+'</div>'+
   '<div class="amt">&#8377;'+esc(p.outstanding)+'</div><div class="chev">&#8250;</div></div>';
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
      (p.said?'<div style="margin-top:8px;color:#8a6300">&ldquo;'+esc(p.said)+'&rdquo;'+(p.when?(' &middot; '+esc(p.when)):'')+'</div>':'')+'</div>';
  }
  let bills=d.open_bills.map(b=>'<div class="bill"><div>'+esc(b.invoice)+
    (b.overdue_days>0?(' <span class="od">'+b.overdue_days+'d</span>'):'')+
    '</div><div>&#8377;'+esc(b.amount)+'</div></div>').join('')||'<div style="color:var(--muted)">No open bills.</div>';
  const tel=telNum(d.whatsapp);
  const call=tel?('<a class="callbtn" href="tel:'+esc(tel)+'">&#128222; Call '+esc(d.name)+'</a>'):'';
  let recent='';
  if(d.recent&&d.recent.length){
    recent='<div class="sect">Recent replies</div><div class="card">'+
      d.recent.map(m=>'<div class="msg">&ldquo;'+esc(m.text)+'&rdquo;'+(m.when?('<span>'+esc(m.when)+'</span>'):'')+'</div>').join('')+'</div>';
  }
  app.innerHTML=
   headerHtml(d.name, d.whatsapp||'no number', true)+
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
