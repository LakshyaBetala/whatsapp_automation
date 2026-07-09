"""Local admin page - the 'who gets reminders' tick-boxes.

One page, no build tools: GET /admin?token=<agent_token> renders every
client with a checkbox (= clients.reminders_enabled, same flag the
STOP/START bot commands flip). Meant for the owner's son / the Tally
operator on the LAN - auth is the business agent_token.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.db import require_db
from app.jobs.reminder_sweep import STYLE_CADENCE
from app.models import PLAN_LABELS, PLAN_LIMITS, Plan, recommend_plan

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def _biz_by_token(token: str) -> dict:
    db = require_db()
    resp = (db.table("businesses")
            .select("id, business_name, weekly_off_day, blackout_dates, "
                    "reminder_style, reminder_custom_line, reminder_hour, msg_language, "
                    "discount_pct, plan, upi_vpa, whatsapp_number")
            .eq("agent_token", token).limit(1).execute())
    if not resp.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return resp.data[0]


def _chunked(items: list, size: int = 100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(token: str = Query(...)):
    biz = _biz_by_token(token)
    db = require_db()

    # All clients (paged) + outstanding totals
    clients: list = []
    start = 0
    while True:
        resp = (db.table("clients")
                .select("id, name, whatsapp_number, reminders_enabled, tally_ledger_name, credit_days")
                .eq("business_id", biz["id"])
                .order("name")
                .range(start, start + 999).execute())
        batch = resp.data or []
        clients.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    today = _dt.date.today()
    totals: dict[str, Decimal] = {}
    overdue_days: dict[str, int] = {}   # client_id -> max days past due
    start = 0
    while True:
        resp = (db.table("bills")
                .select("client_id, outstanding, due_date")
                .eq("business_id", biz["id"])
                .in_("status", ["pending", "partial", "overdue"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        for b in batch:
            cid = b["client_id"]
            totals[cid] = totals.get(cid, Decimal(0)) + Decimal(str(b["outstanding"]))
            dd = b.get("due_date")
            if dd:
                try:
                    od = (today - _dt.date.fromisoformat(str(dd))).days
                    if od > 0:
                        overdue_days[cid] = max(overdue_days.get(cid, 0), od)
                except (TypeError, ValueError):
                    pass
        if len(batch) < 1000:
            break
        start += 1000

    clients.sort(key=lambda c: totals.get(c["id"], Decimal(0)), reverse=True)

    rows = []
    for c in clients:
        out = totals.get(c["id"], Decimal(0))
        out_str = f"₹{out:,.0f}" if out else "-"
        od = overdue_days.get(c["id"], 0)
        od_str = f"{od} din" if od else "-"
        phone = c.get("whatsapp_number") or "❌ no number"
        checked = "checked" if c.get("reminders_enabled", True) else ""
        # Source: Tally-synced (has a ledger name) vs OCR/manual (non-Tally).
        src = "tally" if (c.get("tally_ledger_name") or "").strip() else "nontally"
        cname = (c["name"] or "").replace("&", "&amp;").replace("<", "&lt;")
        nm_attr = cname.replace('"', "&quot;")
        # Non-Tally parties get a manual "Record payment" button (Tally parties
        # are settled from Tally automatically).
        pay_btn = (f'<button class="paybtn" data-cid="{c["id"]}" data-party="{nm_attr}">₹ Pay</button>'
                   if src == "nontally" else "")
        cd = c.get("credit_days")
        cd_val = int(cd) if cd else 0
        cd_label = f"{cd_val}d" if cd_val else "set"
        rows.append(
            f'<tr data-name="{cname.lower()}" data-amt="{float(out)}" data-od="{od}" data-src="{src}">'
            f'<td><input type="checkbox" class="cb" value="{c["id"]}" {checked}></td>'
            f'<td>{cname}</td><td class="amt">{out_str}</td>'
            f'<td class="od">{od_str}</td>'
            f'<td><button class="termbtn" data-cid="{c["id"]}" data-party="{nm_attr}" data-cd="{cd_val}">{cd_label}</button></td>'
            f'<td class="ph">{phone}</td>'
            f'<td><button class="sendbtn" data-party="{nm_attr}">Send now</button> {pay_btn}</td></tr>'
        )

    # ── Reminder settings state (style, send time, weekly off, holidays) ──
    woff = biz.get("weekly_off_day")
    woff_cur = "" if woff is None else str(int(woff))
    woff_json = "null" if woff is None else str(int(woff))
    style = (biz.get("reminder_style") or "standard")
    rhour = biz.get("reminder_hour")
    rhour = 11 if rhour is None else int(rhour)
    cline = biz.get("reminder_custom_line") or ""
    cline_attr = cline.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    festivals = sorted(str(d) for d in (biz.get("blackout_dates") or []))
    festivals_json = json.dumps(festivals)

    # ── Discount + plan usage (this month) ────────────────────────────
    try:
        disc_cur = float(biz.get("discount_pct") or 0)
    except (TypeError, ValueError):
        disc_cur = 0.0
    disc_cur = int(disc_cur) if disc_cur == int(disc_cur) else disc_cur
    try:
        plan_enum = Plan(biz.get("plan") or "starter")
    except ValueError:
        plan_enum = Plan.starter
    plan_price = PLAN_LIMITS[plan_enum].get("price", 0)
    plan_label = PLAN_LABELS.get(plan_enum, str(plan_enum.value).title())
    debtor_cap = PLAN_LIMITS[plan_enum]["debtors"]

    # Active debtors = the recovery base: parties with a WhatsApp number AND an
    # open bill. This is what the owner pays for ("kitne khaate baaki hain") and
    # what drives Meta cost. Cleared/inactive ledgers do NOT count. The
    # reminders on/off toggle is a per-party pause, not part of this count.
    active_debtors = sum(
        1 for c in clients
        if c.get("whatsapp_number")
        and totals.get(c["id"], Decimal(0)) > 0)
    pct_used = min(100, round(active_debtors * 100 / debtor_cap)) if debtor_cap else 0
    over_cap = active_debtors > debtor_cap
    rec_plan = recommend_plan(active_debtors)
    rec_label = PLAN_LABELS.get(rec_plan, str(rec_plan.value).title())
    rec_price = PLAN_LIMITS[rec_plan].get("price", 0)
    plan_mismatch = rec_plan != plan_enum

    # Messages this month - shown small, only as info (not the billing metric).
    period = today.replace(day=1).isoformat()
    _u = (db.table("usage").select("message_count")
          .eq("business_id", biz["id"]).eq("period_month", period).limit(1).execute())
    used = int(_u.data[0]["message_count"]) if _u.data else 0

    tally_n = sum(1 for c in clients if (c.get("tally_ledger_name") or "").strip())
    nontally_n = len(clients) - tally_n

    # Plan meter + ASVA's recommendation.
    if over_cap:
        rec_line = (f'<div class="urec warn">Aapke {active_debtors:,} active customers hain - '
                    f'<b>{rec_label}</b> plan (₹{rec_price:,}/month) lein.</div>')
    elif plan_mismatch:
        rec_line = (f'<div class="urec">ASVA suggestion: {active_debtors:,} active customers ke liye '
                    f'<b>{rec_label}</b> plan (₹{rec_price:,}/month) kaafi hai.</div>')
    else:
        rec_line = f'<div class="urec ok">Aapka {plan_label} plan sahi hai.</div>'
    bar_color = "#c0392b" if over_cap else "#0a7d33"

    style_seg = ''.join(
        f'<button class="{"on" if style == v else ""}" data-v="{v}">{lbl}</button>'
        for v, lbl in [("gentle", "Gentle"), ("standard", "Standard"), ("firm", "Firm")])
    # Base cadence for the per-party schedule preview (truthful to what sends).
    base_cadence_json = json.dumps(STYLE_CADENCE.get(style, STYLE_CADENCE["standard"]))
    msg_lang = (biz.get("msg_language") or "hinglish")
    lang_seg = ''.join(
        f'<button class="{"on" if msg_lang == v else ""}" data-v="{v}">{lbl}</button>'
        for v, lbl in [("hinglish", "Hinglish"), ("english", "English")])
    DOW = [("", "None"), ("0", "Mon"), ("1", "Tue"), ("2", "Wed"),
           ("3", "Thu"), ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]
    woff_seg = ''.join(
        f'<button class="{"on" if woff_cur == v else ""}" data-v="{v}">{lbl}</button>'
        for v, lbl in DOW)
    hour_opts = ''.join(
        f'<option value="{h}" {"selected" if h == rhour else ""}>{h:02d}:00</option>'
        for h in range(24))

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{biz['business_name']} - Reminder Settings</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:system-ui,sans-serif;margin:16px;max-width:860px;color:#222}}
 h2{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:12px}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{padding:6px 10px;border-bottom:1px solid #eee;text-align:left}}
 .amt{{text-align:right;font-variant-numeric:tabular-nums}}
 .od{{text-align:right;color:#a00;font-size:.9em;white-space:nowrap}}
 .ph{{color:#666;font-size:.9em}}
 .bar{{position:sticky;top:0;background:#fff;padding:10px 0;display:flex;gap:8px;align-items:center;border-bottom:2px solid #ddd;z-index:2}}
 input[type=search]{{flex:1;padding:8px;font-size:1em}}
 #sort{{padding:8px;font-size:1em}}
 .help{{color:#555;font-size:.92em;margin:10px 0 0;line-height:1.4}}
 button{{padding:8px 14px;font-size:1em;cursor:pointer}}
 #save,#saveset{{background:#0a7d33;color:#fff;border:0;border-radius:6px}}
 #msg,#setmsg{{color:#0a7d33;font-weight:600;margin-left:6px}}
 .card{{margin:14px 0;padding:14px;border:1px solid #e5e5e5;border-radius:10px;background:#fafafa}}
 .card h3{{margin:0 0 12px}}
 .row{{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}}
 .row>label{{min-width:140px;font-weight:600}}
 .seg{{display:inline-flex;border:1px solid #ccc;border-radius:8px;overflow:hidden}}
 .seg button{{border:0;background:#fff;padding:8px 14px;margin:0}}
 .seg button.on{{background:#0a7d33;color:#fff}}
 .seg button+button{{border-left:1px solid #ccc}}
 #cline{{flex:1;min-width:220px;padding:8px;font-size:1em}}
 #rhour{{padding:8px;font-size:1em}}
 .calhead{{display:flex;align-items:center;gap:12px;margin:6px 0}}
 .calhead span{{font-weight:700;min-width:160px;text-align:center}}
 .calgrid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;max-width:430px}}
 .dow{{text-align:center;font-size:.8em;color:#888;font-weight:600}}
 .day{{text-align:center;padding:8px 0;border:1px solid #eee;border-radius:6px;cursor:pointer;background:#fff}}
 .day.off{{background:#eee;color:#999}}
 .day.hol{{background:#ffd9d9;border-color:#e58;color:#a00;font-weight:700}}
 .day.blank{{border:0;cursor:default;background:transparent}}
 .hint{{color:#777;font-size:.85em;margin-top:6px}}
 .usage{{margin:10px 0;padding:10px 14px;border:1px solid #e5e5e5;border-radius:10px;background:#f4f9f4;font-size:.95em}}
 .ubar{{height:8px;background:#e2e8e2;border-radius:5px;margin-top:6px;overflow:hidden}}
 .ufill{{height:100%;background:#0a7d33}}
 .urec{{margin-top:8px;font-size:.92em;color:#346538}}
 .urec.warn{{color:#a00;font-weight:600}}
 .urec.ok{{color:#346538}}
 .umsg{{margin-top:4px;font-size:.8em;color:#999}}
 .subtabs{{display:flex;gap:6px;margin:16px 0 4px;border-bottom:2px solid #ddd}}
 .subtabs button{{border:1px solid #ddd;border-bottom:0;background:#f2f2f0;border-radius:8px 8px 0 0;padding:9px 18px;font-weight:600;color:#555}}
 .subtabs button.on{{background:#0a7d33;color:#fff;border-color:#0a7d33}}
 .subview{{display:none}}
 .subview.on{{display:block}}
 .sendbtn{{padding:5px 10px;font-size:.85em;border:1px solid #0a7d33;color:#0a7d33;background:#fff;border-radius:6px}}
 .sendbtn:disabled{{opacity:.5;cursor:default}}
 .paybtn{{padding:5px 10px;font-size:.85em;border:1px solid #7d5a0a;color:#7d5a0a;background:#fff;border-radius:6px}}
 .paybtn:disabled{{opacity:.5;cursor:default}}
 .termbtn{{padding:4px 10px;font-size:.85em;border:1px solid #ccc;background:#fff;border-radius:6px}}
 .modal{{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:9}}
 .modal.show{{display:flex}}
 .modalbox{{background:#fff;max-width:420px;width:90%;border-radius:14px;padding:18px 20px}}
 .modalbox h3{{margin:0 0 10px}}
 .msgprev{{white-space:pre-wrap;background:#f6f6f4;border:1px solid #eee;border-radius:8px;padding:12px;font-size:.95em;line-height:1.5;max-height:50vh;overflow:auto}}
</style></head><body>
<h2>{biz['business_name']}</h2>
<div class="sub">Aapke customers, unki baaki aur naye bills. Tick = us party ko reminder jayega. Reminder timing badalni ho to baayein <b>Reminders</b> tab kholein.</div>

<div class="usage">
  <div><b>{plan_label} plan</b> (₹{plan_price:,}/month) -
  <b>{active_debtors:,}</b> / {debtor_cap:,} active customers is month</div>
  <div class="ubar"><div class="ufill" style="width:{pct_used}%;background:{bar_color}"></div></div>
  {rec_line}
  <div class="umsg">{used:,} messages sent is month (auto-managed, not a limit you pay per).</div>
</div>

<div class="subtabs">
 <button class="on" data-sub="tally">Tally bills ({tally_n:,})</button>
 <button data-sub="nontally">Non-Tally bills ({nontally_n:,})</button>
</div>

<div class="help">Har party ke aage <b>tick</b> = us party ko reminder jayega. Neeche se search / sort / Save list kar sakte ho. Non-Tally = photo/OCR se bane bills.</div>
<div class="bar">
 <input type="search" id="q" placeholder="🔍 Naam se dhundo...">
 <select id="sort" onchange="sortRows()">
  <option value="amt">Baaki: zyada pehle</option>
  <option value="od">Overdue: zyada din pehle</option>
  <option value="name">Naam: A to Z</option>
 </select>
 <button onclick="setAll(true)" title="Sabko reminder ON karo">✓ Sab ON</button>
 <button onclick="setAll(false)" title="Sabko reminder OFF karo">✗ Sab OFF</button>
 <button id="save" onclick="save()">💾 Save list</button>
 <span id="msg"></span>
</div>
<table id="ptable"><tr><th>Reminder?</th><th>Party</th><th>Baaki</th><th>Overdue</th><th>Credit days</th><th>WhatsApp</th><th>Actions</th></tr>
{''.join(rows)}
</table>

<div class="modal" id="termmodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Credit days &amp; reminder schedule</h3>
    <div id="termparty" style="font-weight:700;margin-bottom:8px"></div>
    <div class="row" style="margin:6px 0"><label style="min-width:auto;font-weight:600">Credit days</label>
      <input id="termdays" type="number" min="1" max="730" style="width:90px;padding:8px;font-size:1em"> din</div>
    <div class="hint" style="margin:0 0 8px">Tally se aaya hua, ya galat/khaali ho to yahan set karein.</div>
    <div class="msgprev" id="termsched">...</div>
    <div style="margin-top:12px;text-align:right">
      <button onclick="document.getElementById('termmodal').classList.remove('show')">Close</button>
      <button id="termsave" onclick="saveTerms()">💾 Save</button>
    </div>
  </div>
</div>

<script>
const TOKEN = {token!r};
let STYLE = {style!r};
let LANG = {msg_lang!r};
let WOFF = {woff_json};   // 0-6 (Mon..Sun) or null
let FEST = {festivals_json};
let calY, calM;

let SRC = 'tally';   // Dashboard opens on Tally bills
function applyFilter() {{
  const q = document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    const okq = r.dataset.name.includes(q);
    const oks = SRC === 'all' || r.dataset.src === SRC;
    r.style.display = (okq && oks) ? '' : 'none';
  }});
}}
document.getElementById('q').addEventListener('input', applyFilter);
function setAll(v) {{
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    if (r.style.display !== 'none') r.querySelector('.cb').checked = v;
  }});
}}
function sortRows() {{
  const key = document.getElementById('sort').value;
  const rows = [...document.querySelectorAll('#ptable tr[data-name]')];
  rows.sort((a, b) => {{
    if (key === 'name') return a.dataset.name.localeCompare(b.dataset.name);
    if (key === 'od') return parseFloat(b.dataset.od) - parseFloat(a.dataset.od);
    return parseFloat(b.dataset.amt) - parseFloat(a.dataset.amt);
  }});
  const tb = rows.length ? rows[0].parentNode : null;
  if (tb) rows.forEach(r => tb.appendChild(r));
}}
async function save() {{
  const enabled = [...document.querySelectorAll('.cb:checked')].map(c => c.value);
  document.getElementById('msg').textContent = 'Saving...';
  const r = await fetch('/admin/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN, enabled_ids: enabled}})}});
  const d = await r.json();
  document.getElementById('msg').textContent =
    r.ok ? `✅ Saved - ${{d.enabled}} ON, ${{d.disabled}} OFF` : '❌ Save failed';
}}

// sub-tabs filter the party table: Tally vs Non-Tally
document.querySelectorAll('.subtabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.subtabs button').forEach(x => x.classList.toggle('on', x === b));
  SRC = b.dataset.sub;
  applyFilter();
}});
applyFilter();   // apply the default Tally filter on load

async function sendNow(btn) {{
  const party = btn.dataset.party;
  if (!confirm('Abhi ' + party + ' ko reminder bhejein?')) return;
  btn.disabled = true; btn.textContent = 'Sending...';
  try {{
    const r = await fetch('/admin/send-now', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, party: party}})}});
    const d = await r.json();
    btn.textContent = (r.ok && d.sent) ? '✅ Sent' : '❌ ' + (d.detail || 'Failed');
  }} catch (e) {{ btn.textContent = '❌ Failed'; }}
  setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Send now'; }}, 4000);
}}
document.querySelectorAll('.sendbtn').forEach(b => b.onclick = () => sendNow(b));

async function recordPayment(btn) {{
  const amt = prompt('Kitna payment mila? (Rs me)');
  if (amt === null) return;
  const n = parseFloat(amt);
  if (!(n > 0)) {{ alert('Sahi amount likhein.'); return; }}
  btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/record-payment', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: btn.dataset.cid, amount: n}})}});
    const d = await r.json();
    if (r.ok && d.applied > 0) {{
      btn.textContent = '✅ ₹' + d.applied;
      setTimeout(() => location.reload(), 1200);
    }} else {{
      btn.textContent = '❌'; btn.disabled = false;
      alert(d.detail || 'Kuch apply nahi hua.');
      setTimeout(() => {{ btn.textContent = '₹ Pay'; }}, 2000);
    }}
  }} catch (e) {{ btn.textContent = '❌'; btn.disabled = false; }}
}}
document.querySelectorAll('.paybtn').forEach(b => b.onclick = () => recordPayment(b));

// ── Per-party credit days + read-only reminder schedule ──────────────
const BASE_CADENCE = {base_cadence_json};   // reminder timing is ASVA's logic
let TERM_CID = null;
function schedText(cd) {{
  cd = cd || 30;
  const days = BASE_CADENCE.map(d => Math.max(1, Math.min(cd, Math.round(d * cd / 30))));
  const uniq = [...new Set(days)];
  const ov = Math.max(7, Math.round(7 * cd / 30));
  return 'Reminder in dino par jayega (bill ke baad):\\n  ' + uniq.join(', ') + ' din\\n' +
         'Due date ke baad har ~' + ov + ' din ek overdue reminder.\\n\\n' +
         '(Timing ASVA khud set karta hai. Aap sirf credit days badal sakte hain.)';
}}
function openTerms(btn) {{
  TERM_CID = btn.dataset.cid;
  const cd = parseInt(btn.dataset.cd) || 30;
  document.getElementById('termparty').textContent = btn.dataset.party;
  document.getElementById('termdays').value = cd;
  document.getElementById('termsched').textContent = schedText(cd);
  document.getElementById('termmodal').classList.add('show');
}}
document.getElementById('termdays').addEventListener('input', e =>
  document.getElementById('termsched').textContent = schedText(parseInt(e.target.value)));
document.querySelectorAll('.termbtn').forEach(b => b.onclick = () => openTerms(b));
async function saveTerms() {{
  const days = parseInt(document.getElementById('termdays').value);
  if (!(days >= 1 && days <= 730)) {{ alert('Credit days 1 se 730 ke beech likhein.'); return; }}
  const btn = document.getElementById('termsave'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/set-credit-days', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: TERM_CID, days: days}})}});
    if (r.ok) {{ location.reload(); }}
    else {{ btn.disabled = false; btn.textContent = '💾 Save'; alert('Save failed'); }}
  }} catch (e) {{ btn.disabled = false; btn.textContent = '💾 Save'; alert('Save failed'); }}
}}
</script></body></html>"""
    return HTMLResponse(html)


@router.get("/admin/reminders", response_class=HTMLResponse)
async def admin_reminders(token: str = Query(...)):
    """Reminder settings on their own page (its own desktop tab): language, send
    time, weekly off, holidays, custom line, early-pay discount, and preview.
    Timing itself is ASVA's logic (per-party credit days), not editable here."""
    biz = _biz_by_token(token)

    woff = biz.get("weekly_off_day")
    woff_cur = "" if woff is None else str(int(woff))
    woff_json = "null" if woff is None else str(int(woff))
    style = (biz.get("reminder_style") or "standard")
    rhour = biz.get("reminder_hour")
    rhour = 11 if rhour is None else int(rhour)
    cline = biz.get("reminder_custom_line") or ""
    cline_attr = cline.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    festivals = sorted(str(d) for d in (biz.get("blackout_dates") or []))
    festivals_json = json.dumps(festivals)
    try:
        disc_cur = float(biz.get("discount_pct") or 0)
    except (TypeError, ValueError):
        disc_cur = 0.0
    disc_cur = int(disc_cur) if disc_cur == int(disc_cur) else disc_cur
    msg_lang = (biz.get("msg_language") or "hinglish")
    lang_seg = ''.join(
        f'<button class="{"on" if msg_lang == v else ""}" data-v="{v}">{lbl}</button>'
        for v, lbl in [("hinglish", "Hinglish"), ("english", "English")])
    DOW = [("", "None"), ("0", "Mon"), ("1", "Tue"), ("2", "Wed"),
           ("3", "Thu"), ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]
    woff_seg = ''.join(
        f'<button class="{"on" if woff_cur == v else ""}" data-v="{v}">{lbl}</button>'
        for v, lbl in DOW)
    hour_opts = ''.join(
        f'<option value="{h}" {"selected" if h == rhour else ""}>{h:02d}:00</option>'
        for h in range(24))

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{biz['business_name']} - Reminders</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:0;background:#F7F6F3;color:#2F3437}}
 .wrap{{max-width:720px;margin:0 auto;padding:24px 20px}}
 h2{{margin:0 0 4px}} .sub{{color:#787774;margin-bottom:16px;font-size:.95em}}
 .card{{margin:14px 0;padding:20px;border:1px solid #EAEAEA;border-radius:12px;background:#fff}}
 .card h3{{margin:0 0 14px;font-size:1.05rem}}
 .row{{display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap}}
 .row>label{{min-width:150px;font-weight:600}}
 .seg{{display:inline-flex;border:1px solid #ddd;border-radius:8px;overflow:hidden}}
 .seg button{{border:0;background:#fff;padding:9px 16px;margin:0;cursor:pointer;font-size:.95em}}
 .seg button.on{{background:#0a7d33;color:#fff}}
 .seg button+button{{border-left:1px solid #ddd}}
 button{{cursor:pointer}}
 input,select{{padding:8px;font-size:1em;border:1px solid #ddd;border-radius:6px}}
 #cline{{flex:1;min-width:220px}}
 #saveset{{background:#0a7d33;color:#fff;border:0;border-radius:6px;padding:10px 18px;font-size:1em}}
 #setmsg{{color:#0a7d33;font-weight:600;margin-left:8px}}
 .btn2{{background:#fff;border:1px solid #ccc;border-radius:6px;padding:9px 16px;font-size:.95em}}
 .calhead{{display:flex;align-items:center;gap:12px;margin:8px 0}}
 .calhead button{{background:#fff;border:1px solid #ddd;border-radius:6px;padding:6px 12px}}
 .calhead span{{font-weight:700;min-width:160px;text-align:center}}
 .calgrid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;max-width:430px}}
 .dow{{text-align:center;font-size:.8em;color:#999;font-weight:600}}
 .day{{text-align:center;padding:8px 0;border:1px solid #eee;border-radius:6px;cursor:pointer;background:#fff}}
 .day.off{{background:#eee;color:#999}}
 .day.hol{{background:#fdebec;border-color:#e58;color:#9f2f2d;font-weight:700}}
 .day.today{{outline:2px solid #0a7d33;outline-offset:-2px}}
 .day.past{{color:#ccc;background:#fafafa;cursor:default}}
 .day.blank{{border:0;cursor:default;background:transparent}}
 .holist{{margin-top:12px;font-size:.9em;color:#787774}}
 .holchip{{display:inline-block;background:#fdebec;color:#9f2f2d;border-radius:6px;padding:3px 9px;margin:4px 5px 0 0}}
 .hint{{color:#787774;font-size:.85em;margin-top:6px}}
 .modal{{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:9}}
 .modal.show{{display:flex}}
 .modalbox{{background:#fff;max-width:420px;width:90%;border-radius:14px;padding:18px 20px}}
 .msgprev{{white-space:pre-wrap;background:#f6f6f4;border:1px solid #eee;border-radius:8px;padding:12px;font-size:.95em;line-height:1.5;max-height:50vh;overflow:auto}}
</style></head><body>
<div class="wrap">
<h2>Reminders</h2>
<div class="sub">Reminder timing ASVA khud manage karta hai (har party ke credit days ke hisaab se). Aap sirf yeh settings badlein.</div>

<div class="card">
 <div class="row"><label>Message language</label><div class="seg" id="lang">{lang_seg}</div></div>
 <div class="row"><label>Send reminders at</label><select id="rhour">{hour_opts}</select></div>
 <div class="row"><label>Custom line</label>
   <input id="cline" maxlength="120" placeholder="(optional) e.g. Diwali greetings" value="{cline_attr}"></div>
 <div class="row"><label>Early-pay discount</label>
   <input id="disc" type="number" min="0" max="50" step="0.5" value="{disc_cur}" style="width:90px"> %
   <span class="hint" style="margin:0">QR + amount is discount se kam ho jayega. 0 = no discount.</span></div>
 <div class="row"><button type="button" class="btn2" onclick="viewMessage()">View message</button>
   <span class="hint" style="margin:0">Jo settings chuni hain, wahi message dikhega.</span></div>
 <div style="margin-top:14px"><button id="saveset" onclick="saveSettings()">Save settings</button><span id="setmsg"></span></div>
</div>

<div class="card">
 <h3>Holidays</h3>
 <div class="calhead"><button onclick="calMove(-1)">&#9664;</button><span id="calLabel"></span><button onclick="calMove(1)">&#9654;</button></div>
 <div id="calGrid" class="calgrid"></div>
 <div class="hint">Aaj se aage ki date pe tap karke holiday mark karein (red). Purani dates select nahi hongi. Marked dates par reminder skip hoga aur agle working day chala jayega. Save dabana zaroori hai; last saved list hi final hoti hai.</div>
 <div id="holist" class="holist"></div>
</div>
</div>

<div class="modal" id="prevmodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Message preview</h3>
    <div class="msgprev" id="prevtext">...</div>
    <div style="margin-top:12px;text-align:right"><button class="btn2" onclick="document.getElementById('prevmodal').classList.remove('show')">Close</button></div>
  </div>
</div>

<script>
const TOKEN = {token!r};
let STYLE = {style!r};
let LANG = {msg_lang!r};
let WOFF = {woff_json};
let FEST = {festivals_json};
let calY, calM;

document.querySelectorAll('#lang button').forEach(b => b.onclick = () => {{
  LANG = b.dataset.v;
  document.querySelectorAll('#lang button').forEach(x => x.classList.toggle('on', x === b));
}});
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const pad = n => (n < 10 ? '0' : '') + n;
const _t0 = new Date();
const TODAY = _t0.getFullYear() + '-' + pad(_t0.getMonth() + 1) + '-' + pad(_t0.getDate());
FEST = FEST.filter(ds => ds >= TODAY);   // past holidays are irrelevant; drop them
function fmtDate(ds) {{
  const [y, m, d] = ds.split('-').map(Number);
  return d + ' ' + MONTHS[m - 1].slice(0, 3) + ' ' + y;
}}
function renderHolidays() {{
  const el = document.getElementById('holist');
  if (!FEST.length) {{ el.innerHTML = 'Koi holiday nahi. Har din reminder ja sakta hai.'; return; }}
  el.innerHTML = 'Holidays (in dino skip): ' +
    FEST.map(ds => `<span class="holchip">${{fmtDate(ds)}}</span>`).join('');
}}
function renderCal() {{
  document.getElementById('calLabel').textContent = MONTHS[calM] + ' ' + calY;
  const lead = new Date(calY, calM, 1).getDay();
  const days = new Date(calY, calM + 1, 0).getDate();
  let h = ['Su','Mo','Tu','We','Th','Fr','Sa'].map(d => `<div class="dow">${{d}}</div>`).join('');
  for (let i = 0; i < lead; i++) h += '<div class="day blank"></div>';
  for (let d = 1; d <= days; d++) {{
    const ds = calY + '-' + pad(calM + 1) + '-' + pad(d);
    if (ds < TODAY) {{ h += `<div class="day past">${{d}}</div>`; continue; }}  // past = not selectable
    let cls = 'day';
    if (ds === TODAY) cls += ' today';
    if (FEST.includes(ds)) cls += ' hol';
    h += `<div class="${{cls}}" data-d="${{ds}}">${{d}}</div>`;
  }}
  document.getElementById('calGrid').innerHTML = h;
  document.querySelectorAll('#calGrid .day[data-d]').forEach(c => c.onclick = () => {{
    const ds = c.dataset.d;
    if (FEST.includes(ds)) FEST = FEST.filter(x => x !== ds);
    else {{ FEST.push(ds); FEST.sort(); }}
    renderCal();
  }});
  renderHolidays();
}}
function calMove(delta) {{
  calM += delta;
  if (calM < 0) {{ calM = 11; calY--; }}
  if (calM > 11) {{ calM = 0; calY++; }}
  // never go before the current month
  if (calY < _t0.getFullYear() || (calY === _t0.getFullYear() && calM < _t0.getMonth())) {{
    calY = _t0.getFullYear(); calM = _t0.getMonth();
  }}
  renderCal();
}}
const _now = new Date();
calY = _now.getFullYear(); calM = _now.getMonth();
renderCal();

async function saveSettings() {{
  document.getElementById('setmsg').textContent = 'Saving...';
  const r = await fetch('/admin/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      token: TOKEN, reminder_style: STYLE, msg_language: LANG,
      reminder_hour: parseInt(document.getElementById('rhour').value),
      reminder_custom_line: document.getElementById('cline').value,
      discount_pct: parseFloat(document.getElementById('disc').value) || 0,
      weekly_off_day: null, blackout_dates: FEST
    }})}});
  document.getElementById('setmsg').textContent = r.ok ? 'Saved' : 'Save failed';
}}

async function viewMessage() {{
  const box = document.getElementById('prevtext');
  box.textContent = 'Loading...';
  document.getElementById('prevmodal').classList.add('show');
  const p = new URLSearchParams({{
    token: TOKEN, style: STYLE, language: LANG,
    custom_line: document.getElementById('cline').value,
    discount_pct: document.getElementById('disc').value || '0'
  }});
  try {{
    const r = await fetch('/admin/preview?' + p.toString());
    const d = await r.json();
    box.textContent = d.message || 'Preview not available.';
  }} catch (e) {{ box.textContent = 'Preview failed.'; }}
}}
</script></body></html>"""
    return HTMLResponse(html)


class SavePayload(BaseModel):
    token: str
    enabled_ids: list[str]


@router.post("/admin/save")
async def admin_save(payload: SavePayload):
    biz = _biz_by_token(payload.token)
    db = require_db()

    all_ids = []
    start = 0
    while True:
        resp = (db.table("clients").select("id")
                .eq("business_id", biz["id"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        all_ids.extend(c["id"] for c in batch)
        if len(batch) < 1000:
            break
        start += 1000

    enabled = set(payload.enabled_ids) & set(all_ids)
    disabled = [i for i in all_ids if i not in enabled]

    for chunk in _chunked(sorted(enabled)):
        db.table("clients").update({"reminders_enabled": True}).in_("id", chunk).execute()
    for chunk in _chunked(disabled):
        db.table("clients").update({"reminders_enabled": False}).in_("id", chunk).execute()

    return {"enabled": len(enabled), "disabled": len(disabled)}


class SettingsPayload(BaseModel):
    token: str
    weekly_off_day: Optional[int] = None   # Mon=0..Sun=6, or null = open 7 days
    blackout_dates: list[str] = []
    reminder_style: Optional[str] = None   # gentle | standard | firm
    reminder_hour: Optional[int] = None    # 0..23
    reminder_custom_line: Optional[str] = None
    msg_language: Optional[str] = None     # hinglish | english
    discount_pct: Optional[float] = None   # early-payment discount, 0-50


@router.post("/admin/settings")
async def admin_settings(payload: SettingsPayload):
    """Save the reminder schedule: style (+ cadence), send hour, custom line,
    weekly off day and festival/holiday dates."""
    biz = _biz_by_token(payload.token)
    db = require_db()

    update: dict = {}

    woff = payload.weekly_off_day
    if woff is not None and not (0 <= woff <= 6):
        raise HTTPException(status_code=400, detail="weekly_off_day must be 0-6 or null")
    update["weekly_off_day"] = woff

    # Keep only valid YYYY-MM-DD dates, de-duped and sorted.
    clean: set[str] = set()
    for d in payload.blackout_dates:
        try:
            clean.add(_dt.date.fromisoformat(str(d)).isoformat())
        except ValueError:
            continue
    update["blackout_dates"] = sorted(clean)

    # Style also writes the matching cadence (the sweep reads reminder_cadence).
    style = (payload.reminder_style or "standard").lower()
    if style not in STYLE_CADENCE:
        style = "standard"
    update["reminder_style"] = style
    update["reminder_cadence"] = STYLE_CADENCE[style]

    if payload.reminder_hour is not None:
        h = int(payload.reminder_hour)
        if not (0 <= h <= 23):
            raise HTTPException(status_code=400, detail="reminder_hour must be 0-23")
        update["reminder_hour"] = h

    if payload.reminder_custom_line is not None:
        update["reminder_custom_line"] = payload.reminder_custom_line.strip()[:120] or None

    if payload.msg_language is not None:
        update["msg_language"] = "english" if payload.msg_language.lower() == "english" else "hinglish"

    if payload.discount_pct is not None:
        d = float(payload.discount_pct)
        if not (0 <= d <= 50):
            raise HTTPException(status_code=400, detail="discount_pct must be 0-50")
        update["discount_pct"] = round(d, 2)

    db.table("businesses").update(update).eq("id", biz["id"]).execute()

    return {
        "ok": True,
        "style": style,
        "weekly_off_day": woff,
        "reminder_hour": update.get("reminder_hour"),
        "blackout_count": len(clean),
        "discount_pct": update.get("discount_pct"),
    }


@router.get("/admin/preview")
async def admin_preview(
    token: str = Query(...),
    style: str = Query("standard"),
    language: str = Query("hinglish"),
    custom_line: str = Query(""),
    discount_pct: float = Query(0.0),
):
    """Render the exact reminder a customer would receive for the given (unsaved)
    settings, so the owner can preview it before saving."""
    from decimal import Decimal
    from app.models import Lang
    from app.services.templates import apply_discount, inr, render

    biz = _biz_by_token(token)
    lang = (language or "hinglish").lower()
    style_v = (style or "standard").lower()
    if style_v not in STYLE_CADENCE:
        style_v = "standard"

    # Sample figures for a realistic preview.
    sample_amt = Decimal("12500")
    biz_name = biz.get("business_name", "")
    vpa = biz.get("upi_vpa") or "shopupi@bank"
    pay_amount, discount_line = apply_discount(sample_amt, discount_pct, lang)

    template_key = "reminder"
    render_style = style_v
    if lang == "english":
        template_key = "reminder_en"
        render_style = "standard"

    from app.services import upi as upi_svc
    pay_link = upi_svc.upi_link(vpa, biz_name, pay_amount, "2526RTC0203")
    _, body = render(
        template_key, Lang.hi, style=render_style,
        client="Ramesh Traders", business=biz_name,
        invoice_number="2526RTC0203", outstanding=inr(sample_amt),
        days_overdue="5", upi_link=pay_link,
    )
    if discount_line:
        body = f"{body}\n\n{discount_line}"
    cl = (custom_line or "").strip()[:120]
    if cl:
        body = f"{body}\n\n{cl}"
    return {"message": body}


class SendNowPayload(BaseModel):
    token: str
    party: str


@router.post("/admin/send-now")
async def admin_send_now(payload: SendNowPayload):
    """Send a reminder to ONE party immediately (owner presses 'Send now').
    Reuses the bot's REMIND handler so behaviour matches the WhatsApp command."""
    from app.services import bot
    biz = _biz_by_token(payload.token)
    party = (payload.party or "").strip()
    if not party:
        raise HTTPException(status_code=400, detail="party required")
    business = {
        "id": biz["id"],
        "business_name": biz.get("business_name", ""),
        "plan": biz.get("plan", "starter"),
        "whatsapp_number": biz.get("whatsapp_number"),
        "upi_vpa": biz.get("upi_vpa"),
        "discount_pct": biz.get("discount_pct"),
        "msg_language": biz.get("msg_language"),
    }
    try:
        reply = await bot._handle_remind(business, party)
    except Exception as e:
        log.exception("send-now failed for %s", party)
        return {"sent": False, "detail": str(e)[:80]}
    ok = bool(reply) and "nahi mila" not in reply.lower() and "not found" not in reply.lower()
    return {"sent": ok, "detail": reply[:160] if reply else ""}


class CreditDaysPayload(BaseModel):
    token: str
    client_id: str
    days: int


@router.post("/admin/set-credit-days")
async def admin_set_credit_days(payload: CreditDaysPayload):
    """Override/assign a party's credit period. Also recomputes due_date on its
    open bills (due_date = invoice_date + days) so the scaled reminder cadence
    picks up the new terms immediately."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    days = int(payload.days)
    if not (1 <= days <= 730):
        raise HTTPException(status_code=400, detail="days must be 1-730")

    cr = (db.table("clients").select("id")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")

    db.table("clients").update({"credit_days": days}).eq("id", payload.client_id).execute()

    bills = (db.table("bills").select("id, invoice_date")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    updated = 0
    for b in bills:
        try:
            inv = _dt.date.fromisoformat(str(b["invoice_date"]))
            due = (inv + _dt.timedelta(days=days)).isoformat()
            db.table("bills").update({"due_date": due}).eq("id", b["id"]).execute()
            updated += 1
        except (TypeError, ValueError):
            continue
    return {"ok": True, "days": days, "bills_updated": updated}


class RecordPaymentPayload(BaseModel):
    token: str
    client_id: str
    amount: float


@router.post("/admin/record-payment")
async def admin_record_payment(payload: RecordPaymentPayload):
    """Record a payment against a NON-Tally party's open bills (FIFO, oldest
    first), update paid_amount + status, and send the customer a
    'received X, remaining Y' confirmation. Tally parties are excluded - their
    payments flow in from Tally automatically."""
    from decimal import Decimal
    from app.models import Lang, MessageType, Plan
    from app.services import whatsapp
    from app.services.templates import inr, render

    biz = _biz_by_token(payload.token)
    db = require_db()
    amount = round(float(payload.amount), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    cr = (db.table("clients")
          .select("id, name, whatsapp_number, language, tally_ledger_name")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    client = cr.data[0]

    bills = (db.table("bills")
             .select("id, amount, paid_amount, status, invoice_date, tally_voucher_number")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"])
             .order("invoice_date").execute()).data or []
    # NON-Tally bills only (leave Tally-synced bills to the Tally flow).
    bills = [b for b in bills if not (b.get("tally_voucher_number") or "").strip()]
    if not bills:
        return {"applied": 0, "detail": "Koi non-Tally open bill nahi mila."}

    remaining = amount
    applied = 0.0
    for b in bills:
        if remaining <= 0:
            break
        due = float(b["amount"]) - float(b.get("paid_amount") or 0)
        if due <= 0:
            continue
        pay = min(remaining, due)
        new_paid = round(float(b.get("paid_amount") or 0) + pay, 2)
        new_status = "paid" if new_paid >= float(b["amount"]) - 0.01 else "partial"
        db.table("bills").update({"paid_amount": new_paid, "status": new_status}).eq("id", b["id"]).execute()
        remaining -= pay
        applied += pay

    # Remaining outstanding for this party across its non-Tally open bills.
    after = (db.table("bills").select("amount, paid_amount, tally_voucher_number, status")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    still_open = sum(float(b["amount"]) - float(b.get("paid_amount") or 0)
                     for b in after if not (b.get("tally_voucher_number") or "").strip())

    confirmed = False
    if applied > 0 and client.get("whatsapp_number"):
        try:
            lang = Lang(client.get("language") or "hi")
        except Exception:
            lang = Lang.hi
        try:
            _, body = render("payment_confirmation", lang,
                             client=client.get("name", "Customer"),
                             paid_amount=inr(Decimal(str(round(applied, 2)))),
                             outstanding=inr(Decimal(str(round(still_open, 2)))))
            await whatsapp.send_message(
                business_id=biz["id"], to_number=client["whatsapp_number"],
                message_text=body, plan=Plan(biz.get("plan", "starter")),
                message_type=MessageType.payment_confirmation,
                client_id=client["id"], language=lang, channel="shop")
            confirmed = True
        except Exception:
            log.exception("non-Tally payment confirmation failed for %s", client.get("name"))

    return {
        "applied": round(applied, 2),
        "remaining_outstanding": round(still_open, 2),
        "unallocated": round(remaining, 2),
        "confirmation_sent": confirmed,
    }


# ── Shared minimalist styling for the Analytics / Accounts pages ──────────
_CSS = """
 body{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:0;background:#F7F6F3;color:#2F3437}
 .wrap{max-width:940px;margin:0 auto;padding:28px 22px}
 h1{font-size:1.5rem;font-weight:700;letter-spacing:-0.02em;margin:0 0 3px}
 .muted{color:#787774;font-size:.9rem}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:22px 0}
 .card{background:#fff;border:1px solid #EAEAEA;border-radius:12px;padding:22px}
 .kpi .n{font-size:1.8rem;font-weight:700;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
 .kpi .l{color:#787774;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-top:5px}
 h2{font-size:1rem;font-weight:600;margin:26px 0 10px}
 table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #EAEAEA;border-radius:12px;overflow:hidden}
 th,td{padding:11px 14px;text-align:left;border-bottom:1px solid #EAEAEA;font-size:.92rem}
 th{color:#787774;font-weight:600;font-size:.76rem;text-transform:uppercase;letter-spacing:.04em}
 td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
 tr:last-child td{border-bottom:0}
 .age{display:flex;align-items:center;gap:12px;margin:9px 0}
 .age .lbl{width:80px;font-size:.85rem;color:#787774}
 .age .amt{width:120px;text-align:right;font-variant-numeric:tabular-nums;font-size:.88rem}
 .barwrap{flex:1;height:10px;background:#EDEDEA;border-radius:6px;overflow:hidden}
 .barwrap>i{display:block;height:100%;background:#9F2F2D}
 input{font:inherit;padding:10px 12px;border:1px solid #EAEAEA;border-radius:8px;background:#fff;width:100%;box-sizing:border-box}
 label{display:block;font-size:.78rem;color:#787774;margin:16px 0 5px;text-transform:uppercase;letter-spacing:.04em}
 button{font:inherit;background:#111;color:#fff;border:0;border-radius:6px;padding:11px 20px;cursor:pointer;margin-top:18px}
 button:active{transform:scale(.98)}
 .okmsg{color:#346538;font-weight:600;margin-left:10px}
 .hint{color:#787774;font-size:.86rem;line-height:1.5;margin-top:8px}
"""


def _fetch_paged(db, table, cols, biz_id, status_in=None):
    rows, start = [], 0
    while True:
        q = db.table(table).select(cols).eq("business_id", biz_id)
        if status_in:
            q = q.in_("status", status_in)
        batch = q.range(start, start + 999).execute().data or []
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        start += 1000


def _inr(n) -> str:
    n = int(round(float(n or 0)))
    s = str(abs(n))
    if len(s) > 3:
        last3, rest, parts = s[-3:], s[:-3], []
        while len(rest) > 2:
            parts.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    return ("Rs " + s)


@router.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(token: str = Query(...)):
    biz = _biz_by_token(token)
    db = require_db()
    today = _dt.date.today()

    names = {c["id"]: c["name"] for c in _fetch_paged(db, "clients", "id, name", biz["id"])}
    bills = _fetch_paged(db, "bills", "client_id, outstanding, due_date", biz["id"],
                         status_in=["pending", "partial", "overdue"])

    total = Decimal(0)
    by_client: dict = defaultdict(Decimal)
    overdue_parties: set = set()
    od_by_client: dict = defaultdict(int)
    buckets = {"Not due": Decimal(0), "1-30": Decimal(0), "31-60": Decimal(0),
               "61-90": Decimal(0), "90+": Decimal(0)}
    for b in bills:
        out = Decimal(str(b.get("outstanding") or 0))
        if out <= 0:
            continue
        cid = b["client_id"]
        total += out
        by_client[cid] += out
        od = 0
        dd = b.get("due_date")
        if dd:
            try:
                od = (today - _dt.date.fromisoformat(str(dd))).days
            except ValueError:
                od = 0
        if od > 0:
            overdue_parties.add(cid)
            od_by_client[cid] = max(od_by_client[cid], od)
        key = ("Not due" if od <= 0 else "1-30" if od <= 30 else "31-60" if od <= 60
               else "61-90" if od <= 90 else "90+")
        buckets[key] += out

    kpis = [
        (_inr(total), "Total baaki"),
        (str(len(by_client)), "Parties owing"),
        (str(len(overdue_parties)), "Overdue parties"),
        (str(sum(1 for v in by_client.values() if v > 0)), "Open accounts"),
    ]
    kpi_html = "".join(
        f'<div class="card kpi"><div class="n">{n}</div><div class="l">{l}</div></div>'
        for n, l in kpis)

    bmax = max((v for v in buckets.values()), default=Decimal(1)) or Decimal(1)
    age_html = "".join(
        f'<div class="age"><div class="lbl">{k}</div>'
        f'<div class="barwrap"><i style="width:{float(v / bmax * 100):.1f}%"></i></div>'
        f'<div class="amt">{_inr(v)}</div></div>'
        for k, v in buckets.items())

    top = sorted(by_client.items(), key=lambda kv: kv[1], reverse=True)[:12]
    rows_html = "".join(
        f'<tr><td>{names.get(cid, "?")}</td><td class="n">{_inr(amt)}</td>'
        f'<td class="n">{od_by_client.get(cid, 0)} din</td></tr>'
        for cid, amt in top)

    body = (
        f'<h1>Analytics</h1><div class="muted">{biz["business_name"]}</div>'
        f'<div class="grid">{kpi_html}</div>'
        f'<h2>Aging (kitne din se baaki)</h2><div class="card">{age_html}</div>'
        f'<h2>Sabse zyada baaki (top 12)</h2>'
        f'<table><tr><th>Party</th><th class="n">Baaki</th><th class="n">Overdue</th></tr>{rows_html}</table>'
    )
    return HTMLResponse(f'<!doctype html><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<style>{_CSS}</style><div class="wrap">{body}</div>')


@router.get("/admin/accounts", response_class=HTMLResponse)
async def admin_accounts(token: str = Query(...)):
    db = require_db()
    biz = (db.table("businesses")
           .select("id, business_name, upi_vpa, bank_account_name, bank_account_no, bank_ifsc, bank_name")
           .eq("agent_token", token).limit(1).execute())
    if not biz.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    b = biz.data[0]

    def val(k):
        return (b.get(k) or "").replace('"', "&quot;")

    body = f"""<h1>Accounts &amp; Payment</h1>
<div class="muted">{b['business_name']}</div>
<div class="card" style="margin-top:20px;max-width:560px">
 <label>UPI ID (reminder me QR + link isi ka jayega)</label>
 <input id="upi" value="{val('upi_vpa')}" placeholder="e.g. rupeshrtc@oksbi">
 <div class="hint">UPI set hai to har reminder me pay-link + QR apne aap lagta hai.</div>
 <label>Bank account name</label>
 <input id="ban" value="{val('bank_account_name')}" placeholder="RISHAB TRADING COMPANY">
 <label>Account number</label>
 <input id="acc" value="{val('bank_account_no')}" placeholder="0000 0000 0000">
 <label>IFSC</label>
 <input id="ifsc" value="{val('bank_ifsc')}" placeholder="SBIN0000000">
 <label>Bank name</label>
 <input id="bank" value="{val('bank_name')}" placeholder="State Bank of India">
 <div class="hint">UPI na ho to reminder me ye bank details (A/C + IFSC) bheji jayengi.</div>
 <button onclick="save()">Save</button><span id="msg" class="okmsg"></span>
</div>
<script>
const TOKEN = {token!r};
async function save() {{
  document.getElementById('msg').textContent = 'Saving...';
  const r = await fetch('/admin/accounts/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN,
      upi_vpa: document.getElementById('upi').value,
      bank_account_name: document.getElementById('ban').value,
      bank_account_no: document.getElementById('acc').value,
      bank_ifsc: document.getElementById('ifsc').value,
      bank_name: document.getElementById('bank').value}})}});
  document.getElementById('msg').textContent = r.ok ? 'Saved' : 'Save failed';
}}
</script>"""
    return HTMLResponse(f'<!doctype html><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<style>{_CSS}</style><div class="wrap">{body}</div>')


class AccountsPayload(BaseModel):
    token: str
    upi_vpa: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None


@router.post("/admin/accounts/save")
async def admin_accounts_save(payload: AccountsPayload):
    biz = _biz_by_token(payload.token)
    db = require_db()
    update = {
        "upi_vpa": (payload.upi_vpa or "").strip() or None,
        "bank_account_name": (payload.bank_account_name or "").strip() or None,
        "bank_account_no": (payload.bank_account_no or "").strip() or None,
        "bank_ifsc": (payload.bank_ifsc or "").strip().upper() or None,
        "bank_name": (payload.bank_name or "").strip() or None,
    }
    db.table("businesses").update(update).eq("id", biz["id"]).execute()
    return {"ok": True}
