"""ASVA Command Center - the operator's central health + subscription cockpit.

One page to see every business at a glance: online/offline, subscription state,
days to expiry, messages this month, agent version, failed sends today - and to
RENEW or SUSPEND with one click. Gated behind ADMIN_API_KEY (the operator only).

Designed to stay cheap at scale: the data endpoint runs a fixed THREE batch
queries (businesses, this-month usage, today's failed sends) no matter how many
businesses there are - never a per-business loop over the network.
"""
from __future__ import annotations

import datetime as _dt
import logging
import secrets
from collections import Counter

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.db import require_db
from app.models import PLAN_LABELS, PLAN_LIMITS, Plan
from app.services import alerts, monitoring
from app.services import subscription as subs

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ops", tags=["ops"])

ONLINE_MIN = 5          # last_seen within 5 min = online (watcher stamps ~60s)
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _key_ok(key: str | None) -> bool:
    configured = (settings.admin_api_key or "").strip()
    return bool(configured) and bool(key) and secrets.compare_digest(key, configured)


def _plan(biz: dict) -> Plan:
    try:
        return Plan(biz.get("plan") or "starter")
    except ValueError:
        return Plan.starter


def _paged(query_fn, size: int = 1000) -> list:
    rows, start = [], 0
    while True:
        batch = query_fn().range(start, start + size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < size:
            return rows
        start += size


def _fmt_ago(dt: _dt.datetime | None, now: _dt.datetime) -> tuple[str, int]:
    """(human label, minutes_ago). minutes_ago = 10**9 when never seen."""
    if not dt:
        return "never", 10 ** 9
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    mins = int((now - dt).total_seconds() // 60)
    if mins < 1:
        return "just now", 0
    if mins < 60:
        return f"{mins} min ago", mins
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} h ago", mins
    return f"{hrs // 24} d ago", mins


def build_ops_data(db) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    today_ist = _dt.datetime.now(IST).date()
    month_start = today_ist.replace(day=1).isoformat()
    day_start_utc = _dt.datetime.combine(today_ist, _dt.time.min, tzinfo=IST).astimezone(
        _dt.timezone.utc).isoformat()

    # (1) all businesses
    bizes = _paged(lambda: db.table("businesses").select(
        "id, business_name, plan, plan_expires_on, license_key, last_seen, "
        "agent_version, whatsapp_number"))
    # (2) this-month usage, one query -> dict
    used_by: dict = {}
    for r in _paged(lambda: db.table("usage").select("business_id, message_count")
                    .eq("period_month", month_start)):
        used_by[r["business_id"]] = int(r.get("message_count") or 0)
    # (3) today's failed sends, one query -> count per business
    failed_by: Counter = Counter()
    for r in _paged(lambda: db.table("messages").select("business_id")
                    .eq("delivery_status", "failed").gte("created_at", day_start_utc)):
        if r.get("business_id"):
            failed_by[r["business_id"]] += 1

    latest_ver, _mand = _latest_release(db)

    rows = []
    tot = {"businesses": 0, "online": 0, "active": 0, "grace": 0, "suspended": 0,
           "messages_month": 0, "failed_today": 0, "outdated": 0}
    for b in bizes:
        plan = _plan(b)
        limits = PLAN_LIMITS[plan]
        exp = b.get("plan_expires_on")
        status = subs.effective_status(exp, today_ist)
        dleft = subs.days_left(exp, today_ist)
        ls = _parse_ts(b.get("last_seen"))
        label, mins = _fmt_ago(ls, now)
        online = mins <= ONLINE_MIN
        used = used_by.get(b["id"], 0)
        failed = failed_by.get(b["id"], 0)
        ver = b.get("agent_version") or "-"
        version_ok = (ver == latest_ver) or ver == "-"

        tot["businesses"] += 1
        tot["online"] += 1 if online else 0
        tot[status] = tot.get(status, 0) + 1
        tot["messages_month"] += used
        tot["failed_today"] += failed
        tot["outdated"] += 0 if version_ok else 1

        rows.append({
            "id": b["id"],
            "name": b.get("business_name") or "(unnamed)",
            "license_key": b.get("license_key") or "-",
            "plan": plan.value,
            "plan_label": PLAN_LABELS.get(plan, plan.value.title()),
            "price": int(limits.get("price", 0)),
            "status": status,
            "expiry": str(exp)[:10] if exp else None,
            "days_left": dleft,
            "online": online,
            "last_seen_label": label,
            "minutes_ago": mins,
            "version": ver,
            "version_ok": version_ok,
            "messages_used": used,
            "messages_limit": int(limits["messages"]),
            "failed_today": failed,
        })

    # sort: problems first (suspended, then grace, then most days-left), online last
    order = {"suspended": 0, "grace": 1, "active": 2}
    rows.sort(key=lambda r: (order.get(r["status"], 3),
                             r["days_left"] if r["days_left"] is not None else 10 ** 9))
    return {
        "server_time": now.isoformat(),
        "server_version": settings.app_version,
        "latest_version": latest_ver,
        "public_url": settings.public_base_url,
        "totals": tot,
        "businesses": rows,
    }


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _latest_release(db) -> tuple[str, bool]:
    try:
        r = (db.table("app_releases").select("version, mandatory")
             .order("created_at", desc=True).limit(1).execute()).data
        if r:
            return str(r[0]["version"]), bool(r[0].get("mandatory"))
    except Exception:
        pass
    return settings.app_version, False


@router.get("/data")
async def ops_data(key: str = Query("")):
    if not _key_ok(key):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
    return JSONResponse(build_ops_data(require_db()))


@router.get("/health")
async def ops_health(key: str = Query("")):
    """The health center snapshot: system state, per-shop health, 14-day traffic,
    drops, scheduler job heartbeats, and open/recent alerts."""
    if not _key_ok(key):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
    db = require_db()
    health = monitoring.build_health(db)

    base = (settings.platform_wa_url or "").strip()
    bot_ok = None
    if base:
        try:
            async with httpx.AsyncClient(timeout=6) as h:
                r = await h.get(base.rstrip("/") + "/api/wa/status")
                bot_ok = bool(r.json().get("ready"))
        except Exception:
            bot_ok = False
    health["system"] = {
        "server_ok": True,
        "db_ok": True,
        "bot_wa": {"ok": bot_ok, "configured": bool(base)},
        "email": alerts.email_configured(),
    }
    health["alerts"] = {"open": alerts.list_open(db), "recent": alerts.list_recent(db, 30)}
    return JSONResponse(health)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def ops_page(key: str = Query("")):
    if not (settings.admin_api_key or "").strip():
        return HTMLResponse(_DISABLED_HTML, status_code=503)
    if not _key_ok(key):
        return HTMLResponse(_LOGIN_HTML)
    return HTMLResponse(_PAGE_HTML)


# ── HTML ──────────────────────────────────────────────────────────────────
_DISABLED_HTML = """<!doctype html><meta charset="utf-8"><title>ASVA Command Center</title>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;color:#2F3437">
<h2>Command Center is off</h2>
<p>Set <code>ADMIN_API_KEY</code> in the server's <code>.env</code> and restart to enable it.</p>
</body>"""

_LOGIN_HTML = """<!doctype html><meta charset="utf-8"><title>ASVA Command Center</title>
<body style="font-family:'SF Pro Display',system-ui;max-width:360px;margin:80px auto;color:#2F3437">
<h2 style="letter-spacing:-.02em">ASVA Command Center</h2>
<p style="color:#787774">Enter the operator key.</p>
<input id="k" type="password" placeholder="Admin key" autofocus
 style="width:100%;padding:11px 12px;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box;font:inherit">
<button onclick="go()" style="margin-top:12px;width:100%;padding:11px;border:0;border-radius:8px;background:#0a7d33;color:#fff;font:inherit;cursor:pointer">Open</button>
<div id="e" style="color:#c0392b;margin-top:10px;font-size:.9rem"></div>
<script>
function go(){var k=document.getElementById('k').value.trim();if(!k)return;
 location.href='/ops?key='+encodeURIComponent(k);}
document.getElementById('k').addEventListener('keydown',function(e){if(e.key==='Enter')go();});
if(location.search.indexOf('key=')>-1)document.getElementById('e').textContent='Wrong key.';
</script></body>"""

_PAGE_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>ASVA Command Center</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:0;background:#0f1713;color:#e8efe9}
 .top{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;background:#17211b;border-bottom:1px solid #24332a}
 .top h1{font-size:1.15rem;margin:0;letter-spacing:.02em;font-weight:800}
 .top .meta{color:#8fae9c;font-size:.82rem}
 .wrap{padding:18px 22px;max-width:1500px;margin:0 auto}
 .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
 .kpi{background:#17211b;border:1px solid #24332a;border-radius:12px;padding:14px 16px}
 .kpi .n{font-size:1.7rem;font-weight:700;font-variant-numeric:tabular-nums}
 .kpi .l{color:#8fae9c;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
 .kpi.warn .n{color:#f0b849}.kpi.bad .n{color:#e2574c}.kpi.good .n{color:#46d67e}
 .tablewrap{overflow-x:auto;background:#17211b;border:1px solid #24332a;border-radius:12px}
 table{width:100%;border-collapse:collapse;min-width:1050px}
 th,td{padding:11px 13px;text-align:left;border-bottom:1px solid #223029;font-size:.9rem;white-space:nowrap}
 th{color:#8fae9c;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em}
 tr:last-child td{border-bottom:0}
 tbody tr:hover td{background:#1c281f}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
 .dot.on{background:#46d67e}.dot.off{background:#556}
 .pill{display:inline-block;font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:9999px;text-transform:uppercase;letter-spacing:.04em}
 .pill.active{background:#123524;color:#46d67e}.pill.grace{background:#3a2f10;color:#f0b849}.pill.suspended{background:#3a1613;color:#ff6a5c}
 .lk{font-family:'SF Mono',Consolas,monospace;font-size:.8rem;color:#9db8a8}
 .btn{font:inherit;font-size:.82rem;border:1px solid #2c5c42;background:#123524;color:#cfe6d8;border-radius:6px;padding:5px 10px;cursor:pointer}
 .btn:hover{background:#173f2a}.btn.sus{border-color:#5c2c2c;background:#341613;color:#f2b8b0}.btn.sus:hover{background:#4a1e19}
 .warnv{color:#f0b849}
 .muted{color:#7c9787;font-size:.82rem}
 select.pl{font:inherit;font-size:.82rem;background:#0f1713;color:#cfe6d8;border:1px solid #2c5c42;border-radius:6px;padding:5px}
 #msg{color:#46d67e;font-size:.85rem;min-height:18px;margin:8px 0}
 .add{font:inherit;font-size:.85rem;font-weight:700;border:0;background:#0a7d33;color:#fff;border-radius:7px;padding:8px 14px;cursor:pointer}
 .add:hover{background:#0c8f3b}.add:active{transform:scale(.97)}
 .modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:flex-start;justify-content:center;z-index:50;overflow:auto}
 .modal.show{display:flex}
 .card{background:#17211b;border:1px solid #24332a;border-radius:14px;padding:24px;margin:60px 16px;width:100%;max-width:440px}
 .card h3{margin:0 0 4px;font-size:1.1rem}.card p.sub{margin:0 0 16px;color:#8fae9c;font-size:.85rem}
 .fld{margin-bottom:13px}.fld label{display:block;font-size:.75rem;color:#8fae9c;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}
 .fld input,.fld select{width:100%;box-sizing:border-box;font:inherit;background:#0f1713;color:#e8efe9;border:1px solid #2c5c42;border-radius:8px;padding:9px 11px}
 .row2{display:flex;gap:10px}.row2>*{flex:1}
 .modal .go{width:100%;margin-top:6px;font:inherit;font-weight:700;border:0;border-radius:8px;background:#0a7d33;color:#fff;padding:11px;cursor:pointer}
 .modal .go:active{transform:scale(.98)}
 .modal .x{float:right;color:#8fae9c;cursor:pointer;font-size:1.3rem;line-height:1;background:none;border:0}
 .err{color:#ff6a5c;font-size:.85rem;min-height:16px;margin-top:4px}
 .result .kv{margin:9px 0}.result .kv .l{font-size:.72rem;color:#8fae9c;text-transform:uppercase;letter-spacing:.05em}
 .result .kv .v{font-family:'SF Mono',Consolas,monospace;font-size:.9rem;color:#cfe6d8;word-break:break-all;background:#0f1713;border:1px solid #24332a;border-radius:7px;padding:8px 10px;margin-top:3px;display:flex;justify-content:space-between;gap:8px;align-items:center}
 .copy{font:inherit;font-size:.72rem;border:1px solid #2c5c42;background:#123524;color:#cfe6d8;border-radius:5px;padding:3px 8px;cursor:pointer;flex:none}
 .warnbox{background:#3a2f10;color:#f0d79a;border-radius:8px;padding:9px 11px;font-size:.8rem;margin:12px 0}
 .tabs{display:flex;gap:8px;margin:4px 0 16px}
 .tab{font:inherit;font-size:.9rem;font-weight:700;border:1px solid #24332a;background:#17211b;color:#8fae9c;border-radius:8px;padding:8px 16px;cursor:pointer}
 .tab.on{background:#123524;color:#46d67e;border-color:#2c5c42}
 .sys{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px}
 .sys .c{background:#17211b;border:1px solid #24332a;border-radius:12px;padding:14px 16px;display:flex;align-items:center;gap:11px}
 .sys .c .big{font-size:1.5rem;line-height:1}
 .sys .c .l{font-size:.7rem;color:#8fae9c;text-transform:uppercase;letter-spacing:.05em}
 .sys .c .s{font-weight:700;font-size:.95rem}
 .ok{color:#46d67e}.down{color:#e2574c}.unk{color:#8fae9c}
 .jobs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
 .job{font-size:.78rem;background:#17211b;border:1px solid #24332a;border-radius:9999px;padding:4px 11px;color:#9db8a8}
 .job.stale{border-color:#5c2c2c;color:#ff6a5c}
 .traf{display:flex;align-items:flex-end;gap:3px;height:96px;background:#17211b;border:1px solid #24332a;border-radius:12px;padding:12px}
 .bar{flex:1;display:flex;flex-direction:column-reverse;min-width:6px}
 .bar .s{background:#2f8f52}.bar .f{background:#e2574c}
 .alertbox{background:#17211b;border:1px solid #24332a;border-radius:12px;margin-bottom:16px;overflow:hidden}
 .al{display:flex;gap:10px;align-items:flex-start;padding:10px 13px;border-bottom:1px solid #223029}
 .al:last-child{border-bottom:0}
 .al .sev{font-size:.64rem;font-weight:700;padding:2px 8px;border-radius:9999px;text-transform:uppercase;flex:none;margin-top:1px}
 .al .sev.critical{background:#3a1613;color:#ff6a5c}.al .sev.warn{background:#3a2f10;color:#f0b849}.al .sev.info{background:#123049;color:#5fb0e6}
 .al .t{font-weight:600}.al .b{color:#8fae9c;font-size:.85rem;margin-top:2px}
 .al .age{color:#7c9787;font-size:.78rem;margin-left:auto;flex:none}
 .allclear{color:#46d67e;padding:18px;text-align:center;font-weight:600}
 .sect{font-size:.74rem;color:#8fae9c;text-transform:uppercase;letter-spacing:.06em;margin:6px 2px 8px}
 /* Pairing code - the hero of onboarding. Big enough to read aloud over a phone. */
 .code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:2.3rem;letter-spacing:.16em;
   font-weight:700;color:#8ef0b0;background:#0f1a13;border:1px solid #2f8f52;border-radius:14px;
   padding:18px 10px;text-align:center;margin:12px 0 6px;user-select:all}
 .codehint{color:#8fae9c;font-size:.82rem;text-align:center;margin-bottom:14px}
 .steps{background:#17211b;border:1px solid #24332a;border-radius:12px;padding:14px 16px;margin-bottom:14px}
 .steps ol{margin:0;padding-left:20px} .steps li{margin:5px 0;color:#cfe3d6}
 details.adv{margin-top:6px;border-top:1px solid #223029;padding-top:10px}
 details.adv summary{cursor:pointer;color:#8fae9c;font-size:.82rem}
</style></head><body>
<div class="top">
  <h1>ASVA Command Center</h1>
  <div class="meta"><button class="add" onclick="openAdd()">+ Add business</button>
    &nbsp;&nbsp;<span id="clock"></span> &middot; server v<span id="sv"></span></div>
</div>

<div class="modal" id="addModal">
 <div class="card" id="addCard">
  <button class="x" onclick="closeAdd()">&times;</button>
  <h3>Onboard a shop</h3>
  <p class="sub">Creates the business, its licence key and a private agent token.</p>
  <div class="fld"><label>Shop / business name</label><input id="f_biz" placeholder="Rishab Trading Company"></div>
  <div class="fld"><label>Owner name</label><input id="f_owner" placeholder="Owner"></div>
  <div class="fld"><label>WhatsApp number (10-digit)</label><input id="f_wa" inputmode="numeric" placeholder="9876543210"></div>
  <div class="row2">
   <div class="fld"><label>Plan</label><select id="f_plan"></select></div>
   <div class="fld"><label>Paid months</label><input id="f_months" type="number" min="1" max="60" value="1"></div>
  </div>
  <div class="err" id="addErr"></div>
  <button class="go" id="addGo" onclick="doAdd()">Create business</button>
 </div>
 <div class="card result" id="addResult" style="display:none">
  <button class="x" onclick="closeAdd()">&times;</button>
  <h3>Shop created</h3>
  <p class="sub" id="r_name"></p>
  <div class="code" id="r_code"></div>
  <div class="codehint">Read this code to the shop over the phone. Valid <span id="r_codeexp">24 hours</span>, one use only.</div>
  <div class="steps"><ol>
    <li>They open <b>tryasva.com</b> and click <b>Download</b>.</li>
    <li>They run the installer and type this code.</li>
    <li>They pick their Tally company and scan their own WhatsApp.</li>
  </ol></div>
  <div class="kv"><div class="l">Licence key (for support)</div><div class="v"><span id="r_lk"></span><button class="copy" onclick="cp('r_lk')">Copy</button></div></div>
  <details class="adv"><summary>Advanced: manual setup (old flow)</summary>
    <div class="kv"><div class="l">Agent token (secret)</div><div class="v"><span id="r_tok"></span><button class="copy" onclick="cp('r_tok')">Copy</button></div></div>
    <div class="kv"><div class="l">Shop config.json</div><div class="v"><span id="r_cfg"></span><button class="copy" onclick="cp('r_cfg')">Copy</button></div></div>
  </details>
  <button class="go" onclick="closeAdd()">Done</button>
 </div>
</div>

<div class="modal" id="pairModal">
 <div class="card">
  <button class="x" onclick="closePair()">&times;</button>
  <h3>Pairing code</h3>
  <p class="sub" id="p_name"></p>
  <div class="code" id="p_code"></div>
  <div class="codehint">Valid 24 hours, one use only.</div>
  <div class="steps"><ol>
    <li>Install ASVA on that shop's laptop (or reinstall over the old one).</li>
    <li>Type this code when it asks.</li>
    <li>It reconnects to <b>this same business</b>, so every reminder and setting is kept.</li>
  </ol></div>
  <button class="go" onclick="closePair()">Done</button>
 </div>
</div>
<div class="wrap">
  <div id="msg"></div>
  <div class="tabs">
    <button class="tab on" id="tabH" onclick="showTab('health')">Health</button>
    <button class="tab" id="tabS" onclick="showTab('subs')">Subscriptions</button>
  </div>

  <div id="tab_health">
    <div class="sys" id="sys"></div>
    <div class="jobs" id="jobs"></div>
    <div class="kpis" id="hkpis"></div>
    <div class="sect">Needs attention</div>
    <div class="alertbox" id="alerts"></div>
    <div class="sect">Traffic - 14 days (green sent / red failed)</div>
    <div class="traf" id="traf"></div>
    <div class="sect" style="margin-top:16px">Per shop, today</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Shop</th><th>Status</th><th>Agent</th><th>WhatsApp</th>
        <th class="num">Sent</th><th class="num">Failed</th><th class="num">Blocked</th>
        <th class="num">Queued</th><th>Last seen</th></tr></thead>
      <tbody id="hrows"></tbody>
    </table></div>
    <div class="muted" style="margin-top:10px">Refreshes every 30s. The watchdog also emails you the moment something critical drops.</div>
  </div>

  <div id="tab_subs" style="display:none">
  <div class="kpis" id="kpis"></div>
  <div class="tablewrap"><table>
   <thead><tr>
     <th>Business</th><th>Status</th><th>Plan</th><th>Expiry</th><th class="num">Days</th>
     <th>Last seen</th><th>Version</th><th class="num">Msgs (mo)</th><th class="num">Fail (today)</th>
     <th>Renew</th><th>Cut off</th><th>Pair</th>
   </tr></thead>
   <tbody id="rows"></tbody>
  </table></div>
  <div class="muted" style="margin-top:10px">Auto-refreshes every 30s. "Days" is time to expiry (negative = past). Online = agent seen in the last 5 min.</div>
  </div>
</div>
<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
const inr = n => (n||0).toLocaleString('en-IN');
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
const PLANS=['starter','growth','pro','max'], PLABEL={starter:'Basic',growth:'Growth',pro:'Pro',max:'Custom'};
let PUBLIC_URL='';

// A failed data/health fetch is one of two things. A 401/403 means the operator key
// went stale (a redeploy rotates it), so bounce to the key screen where a single
// re-entry fixes it - not the old dead-end "reload with the key" that just kept
// failing. Anything else is a transient blip the 30s auto-refresh clears on its own.
function fetchFailed(r,label){
  if(r.status===401||r.status===403){ location.href='/ops'; return; }
  document.getElementById('msg').textContent=(label||'Data')+' is slow right now. Retrying...';
}

async function load(){
  try{
    const r = await fetch('/ops/data?key='+encodeURIComponent(KEY));
    if(!r.ok){ fetchFailed(r,'Command Center'); return; }
    const d = await r.json();
    PUBLIC_URL = d.public_url || '';
    document.getElementById('sv').textContent = d.server_version;
    const t = d.totals;
    const K=[['businesses','Businesses',''],['online','Online','good'],
      ['active','Active','good'],['grace','Grace','warn'],['suspended','Suspended','bad'],
      ['messages_month','Messages (mo)',''],['failed_today','Failed (today)', t.failed_today?'bad':''],
      ['outdated','Outdated','warn']];
    document.getElementById('kpis').innerHTML = K.map(k=>
      '<div class="kpi '+(k[2])+'"><div class="n">'+inr(t[k[0]])+'</div><div class="l">'+k[1]+'</div></div>').join('');
    document.getElementById('rows').innerHTML = d.businesses.map(rowHtml).join('') ||
      '<tr><td colspan="12" class="muted" style="padding:22px">No businesses yet.</td></tr>';
  }catch(e){document.getElementById('msg').textContent='Could not load. Retrying...';}
}
function rowHtml(b){
  const days = (b.days_left==null)?'-':b.days_left;
  const verCls = b.version_ok?'':'warnv';
  const plopts = PLANS.map(p=>'<option value="'+p+'"'+(p===b.plan?' selected':'')+'>'+PLABEL[p]+'</option>').join('');
  return '<tr>'+
    '<td><div><span class="dot '+(b.online?'on':'off')+'"></span>'+esc(b.name)+'</div>'+
      '<div class="lk">'+esc(b.license_key)+'</div></td>'+
    '<td><span class="pill '+b.status+'">'+b.status+'</span></td>'+
    '<td><select class="pl" onchange="setPlan(\''+b.id+'\',this.value)">'+plopts+'</select>'+
      '<div class="muted">&#8377;'+inr(b.price)+'</div></td>'+
    '<td>'+(b.expiry||'-')+'</td>'+
    '<td class="num">'+days+'</td>'+
    '<td>'+esc(b.last_seen_label)+'</td>'+
    '<td class="'+verCls+'">'+esc(b.version)+'</td>'+
    '<td class="num">'+inr(b.messages_used)+' / '+inr(b.messages_limit)+'</td>'+
    '<td class="num">'+(b.failed_today||0)+'</td>'+
    '<td><button class="btn" onclick="renew(\''+b.id+'\',1)">+1 mo</button></td>'+
    '<td><button class="btn sus" onclick="suspend(\''+b.id+'\',\''+esc(b.name).replace(/\\/g,'')+'\')">Suspend</button></td>'+
    '<td><button class="btn" onclick="pairCode(\''+b.id+'\',\''+esc(b.name).replace(/\\/g,'')+'\')">Get code</button></td>'+
  '</tr>';
}
function flash(t){var m=document.getElementById('msg');m.textContent=t;setTimeout(()=>{if(m.textContent===t)m.textContent='';},4000);}
async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({})); return {ok:r.ok,j:j};
}
async function renew(id,months){
  const x=await post('/license/renew',{admin_key:KEY,business_id:id,months:months});
  flash(x.ok?('Renewed until '+x.j.renewed_until):(x.j.detail||'Renew failed')); load();
}
async function setPlan(id,plan){
  const x=await post('/license/set-plan',{admin_key:KEY,business_id:id,plan:plan});
  flash(x.ok?('Plan set to '+plan):(x.j.detail||'Plan change failed')); load();
}
async function suspend(id,name){
  if(!confirm('Suspend "'+name+'" now? Their sends stop immediately. Reversible with Renew.'))return;
  const x=await post('/license/suspend',{admin_key:KEY,business_id:id});
  flash(x.ok?'Suspended':(x.j.detail||'Suspend failed')); load();
}
// ── Pairing codes ─────────────────────────────────────────────────────────
// Re-pair an EXISTING shop onto a fresh install. The code binds to the same
// business_id, so its reminders and settings (which live in the DB) carry over
// untouched - this is the safe way to move a shop off an older install.
async function pairCode(id,name){
  const x=await post('/license/mint-code',{admin_key:KEY,business_id:id,ttl_hours:24});
  if(!x.ok){flash(x.j.detail||'Could not create a code');return;}
  document.getElementById('p_name').textContent=name;
  document.getElementById('p_code').textContent=x.j.code_display||x.j.code;
  document.getElementById('pairModal').classList.add('show');
}
function closePair(){document.getElementById('pairModal').classList.remove('show');}

// ── Add business ──────────────────────────────────────────────────────────
function openAdd(){
  document.getElementById('f_plan').innerHTML =
    PLANS.map(p=>'<option value="'+p+'">'+PLABEL[p]+'</option>').join('');
  document.getElementById('addErr').textContent='';
  ['f_biz','f_owner','f_wa'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f_months').value='1';
  document.getElementById('addCard').style.display='';
  document.getElementById('addResult').style.display='none';
  document.getElementById('addModal').classList.add('show');
  document.getElementById('f_biz').focus();
}
function closeAdd(){document.getElementById('addModal').classList.remove('show');}
async function doAdd(){
  const wa=document.getElementById('f_wa').value.replace(/\D/g,'');
  const body={admin_key:KEY,
    owner_name:document.getElementById('f_owner').value.trim(),
    business_name:document.getElementById('f_biz').value.trim(),
    whatsapp_number:wa,
    plan:document.getElementById('f_plan').value,
    months:parseFloat(document.getElementById('f_months').value)||1};
  if(!body.business_name){document.getElementById('addErr').textContent='Enter a shop name.';return;}
  if(wa.length<10){document.getElementById('addErr').textContent='Enter a 10-digit WhatsApp number.';return;}
  const btn=document.getElementById('addGo');btn.disabled=true;btn.textContent='Creating...';
  const x=await post('/license/create-business',body);
  btn.disabled=false;btn.textContent='Create business';
  if(!x.ok){document.getElementById('addErr').textContent=x.j.detail||'Could not create.';return;}
  const cfg=JSON.stringify({
    backend_url:PUBLIC_URL||'https://tryasva.com',
    business_id:x.j.business_id,
    agent_token:x.j.agent_token,
    company_name:'YOUR TALLY COMPANY NAME',
    tally_host:'localhost',
    tally_port:9000,
    watch_interval_seconds:300,
    folder_poll_seconds:8,
    bill_pdf_dir:'C:\\ASVA\\bills'
  },null,2);
  document.getElementById('r_name').textContent=x.j.business_name+' - '+PLABEL[x.j.plan]+', paid till '+x.j.plan_expires_on;
  document.getElementById('r_code').textContent=x.j.pairing_code_display||x.j.pairing_code||'(code unavailable)';
  document.getElementById('r_lk').textContent=x.j.license_key;
  document.getElementById('r_tok').textContent=x.j.agent_token;
  document.getElementById('r_cfg').textContent=cfg;
  document.getElementById('addCard').style.display='none';
  document.getElementById('addResult').style.display='';
  load();
}
function cp(id){
  const t=document.getElementById(id).textContent;
  navigator.clipboard.writeText(t).then(()=>flash('Copied'),()=>flash('Copy failed - select manually'));
}
document.getElementById('addModal').addEventListener('click',function(e){if(e.target===this)closeAdd();});

// ── Health center ─────────────────────────────────────────────────────────
let TAB='health';
function showTab(t){TAB=t;
  document.getElementById('tab_health').style.display=(t==='health')?'':'none';
  document.getElementById('tab_subs').style.display=(t==='subs')?'':'none';
  document.getElementById('tabH').classList.toggle('on',t==='health');
  document.getElementById('tabS').classList.toggle('on',t==='subs');
  if(t==='health')loadHealth(); else load();
}
function ago(m){if(m==null)return '-';if(m>=1e8)return 'never';if(m<1)return 'now';if(m<60)return m+'m';var h=Math.floor(m/60);if(h<24)return h+'h';return Math.floor(h/24)+'d';}
function minsSince(iso){try{return Math.floor((Date.now()-new Date(iso).getTime())/60000);}catch(e){return null;}}
function sysCard(icon,label,state,cls){return '<div class="c"><div class="big '+cls+'">'+icon+'</div><div><div class="l">'+label+'</div><div class="s '+cls+'">'+esc(state)+'</div></div></div>';}
async function loadHealth(){
  try{
    const r=await fetch('/ops/health?key='+encodeURIComponent(KEY));
    if(!r.ok){ fetchFailed(r,'Health'); return; }
    const d=await r.json();
    const sy=d.system||{}, bw=sy.bot_wa||{};
    const bot = !bw.configured ? ['not set','unk'] : (bw.ok ? ['Connected','ok'] : ['DOWN','down']);
    const em = sy.email ? ['On','ok'] : ['Off','unk'];
    document.getElementById('sys').innerHTML =
      sysCard('●','Server','Up','ok')+
      sysCard('●','Database','Up','ok')+
      sysCard('●','Bot WhatsApp',bot[0],bot[1])+
      sysCard('✉','Email alerts',em[0],em[1]);
    document.getElementById('jobs').innerHTML=(d.jobs||[]).map(j=>
      '<span class="job'+(j.stale?' stale':'')+'">'+esc(j.name)+' · '+ago(j.mins_ago)+(j.stale?' (stalled)':'')+'</span>').join('')
      || '<span class="muted">No job runs recorded yet.</span>';
    const t=d.totals||{};
    const K=[['businesses','Shops',''],['online','Online','good'],
      ['sent_today','Sent today','good'],['failed_today','Failed today',t.failed_today?'bad':''],
      ['blocked_today','Blocked','warn'],['queued_now','Queued now',t.queued_now?'warn':'']];
    const open=(d.alerts&&d.alerts.open)||[];
    let kpi=K.map(k=>'<div class="kpi '+k[2]+'"><div class="n">'+inr(t[k[0]]||0)+'</div><div class="l">'+k[1]+'</div></div>').join('');
    kpi+='<div class="kpi '+((t.wa_down||0)?'bad':'good')+'"><div class="n">'+inr(t.wa_down||0)+'</div><div class="l">WhatsApp down</div></div>';
    kpi+='<div class="kpi '+(open.length?'bad':'good')+'"><div class="n">'+open.length+'</div><div class="l">Open alerts</div></div>';
    document.getElementById('hkpis').innerHTML=kpi;
    document.getElementById('alerts').innerHTML = open.length ? open.map(a=>
      '<div class="al"><span class="sev '+esc(a.severity)+'">'+esc(a.severity)+'</span>'+
      '<div><div class="t">'+esc(a.title)+'</div>'+(a.body?'<div class="b">'+esc(a.body)+'</div>':'')+'</div>'+
      '<span class="age">'+ago(minsSince(a.created_at))+'</span></div>').join('')
      : '<div class="allclear">All clear. Nothing needs attention.</div>';
    const tr=d.traffic||[]; const mx=Math.max(1,...tr.map(x=>x.sent+x.failed));
    document.getElementById('traf').innerHTML=tr.map(x=>
      '<div class="bar" title="'+x.date+': '+x.sent+' sent, '+x.failed+' failed">'+
      '<div class="s" style="height:'+Math.round(72*x.sent/mx)+'px"></div>'+
      '<div class="f" style="height:'+Math.round(72*x.failed/mx)+'px"></div></div>').join('');
    document.getElementById('hrows').innerHTML=(d.businesses||[]).map(b=>{
      const wa=(b.wa_ready===true)?'<span class="dot on"></span>on'
        :(b.wa_ready===false)?'<span class="dot" style="background:#e2574c;display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px"></span>down'
        :'<span class="dot off"></span>?';
      return '<tr><td>'+esc(b.name)+'</td>'+
        '<td><span class="pill '+b.status+'">'+b.status+'</span></td>'+
        '<td><span class="dot '+(b.online?'on':'off')+'"></span>'+(b.online?'online':'offline')+'</td>'+
        '<td>'+wa+'</td>'+
        '<td class="num">'+inr(b.sent_today)+'</td>'+
        '<td class="num '+(b.failed_today?'warnv':'')+'">'+inr(b.failed_today)+'</td>'+
        '<td class="num">'+inr(b.blocked_today)+'</td>'+
        '<td class="num '+(b.queued?'warnv':'')+'">'+inr(b.queued)+'</td>'+
        '<td>'+ago(b.last_seen_min)+'</td></tr>';
    }).join('') || '<tr><td colspan="9" class="muted" style="padding:22px">No shops yet.</td></tr>';
  }catch(e){document.getElementById('msg').textContent='Could not load health. Retrying...';}
}

setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString();},1000);
showTab('health');
setInterval(()=>{ if(TAB==='health') loadHealth(); else load(); }, 30000);
</script></body></html>"""
