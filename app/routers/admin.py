"""Local admin page — the 'who gets reminders' tick-boxes.

One page, no build tools: GET /admin?token=<agent_token> renders every
client with a checkbox (= clients.reminders_enabled, same flag the
STOP/START bot commands flip). Meant for the owner's son / the Tally
operator on the LAN — auth is the business agent_token.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.db import require_db

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def _biz_by_token(token: str) -> dict:
    db = require_db()
    resp = db.table("businesses").select("id, business_name").eq("agent_token", token).limit(1).execute()
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
                .select("id, name, whatsapp_number, reminders_enabled")
                .eq("business_id", biz["id"])
                .order("name")
                .range(start, start + 999).execute())
        batch = resp.data or []
        clients.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    totals: dict[str, Decimal] = {}
    start = 0
    while True:
        resp = (db.table("bills")
                .select("client_id, outstanding")
                .eq("business_id", biz["id"])
                .in_("status", ["pending", "partial", "overdue"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        for b in batch:
            totals[b["client_id"]] = totals.get(b["client_id"], Decimal(0)) + Decimal(str(b["outstanding"]))
        if len(batch) < 1000:
            break
        start += 1000

    clients.sort(key=lambda c: totals.get(c["id"], Decimal(0)), reverse=True)

    rows = []
    for c in clients:
        out = totals.get(c["id"], Decimal(0))
        out_str = f"₹{out:,.0f}" if out else "—"
        phone = c.get("whatsapp_number") or "❌ no number"
        checked = "checked" if c.get("reminders_enabled", True) else ""
        rows.append(
            f'<tr data-name="{(c["name"] or "").lower()}">'
            f'<td><input type="checkbox" class="cb" value="{c["id"]}" {checked}></td>'
            f'<td>{c["name"]}</td><td class="amt">{out_str}</td><td class="ph">{phone}</td></tr>'
        )

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{biz['business_name']} — Reminder Settings</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:system-ui,sans-serif;margin:16px;max-width:860px}}
 h2{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:12px}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{padding:6px 10px;border-bottom:1px solid #eee;text-align:left}}
 .amt{{text-align:right;font-variant-numeric:tabular-nums}}
 .ph{{color:#666;font-size:.9em}}
 .bar{{position:sticky;top:0;background:#fff;padding:10px 0;display:flex;gap:8px;align-items:center;border-bottom:2px solid #ddd}}
 input[type=search]{{flex:1;padding:8px;font-size:1em}}
 button{{padding:8px 14px;font-size:1em;cursor:pointer}}
 #save{{background:#0a7d33;color:#fff;border:0;border-radius:6px}}
 #msg{{color:#0a7d33;font-weight:600}}
</style></head><body>
<h2>{biz['business_name']}</h2>
<div class="sub">✅ tick = reminder jayega &nbsp;|&nbsp; ⬜ untick = nahi jayega. Save dabana mat bhoolna.</div>
<div class="bar">
 <input type="search" id="q" placeholder="Naam se dhundo...">
 <button onclick="setAll(true)">Sab ON</button>
 <button onclick="setAll(false)">Sab OFF</button>
 <button id="save" onclick="save()">💾 Save</button>
 <span id="msg"></span>
</div>
<table><tr><th></th><th>Party</th><th>Baaki</th><th>WhatsApp</th></tr>
{''.join(rows)}
</table>
<script>
const TOKEN = {token!r};
document.getElementById('q').addEventListener('input', e => {{
  const q = e.target.value.toLowerCase();
  document.querySelectorAll('tr[data-name]').forEach(r =>
    r.style.display = r.dataset.name.includes(q) ? '' : 'none');
}});
function setAll(v) {{
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    if (r.style.display !== 'none') r.querySelector('.cb').checked = v;
  }});
}}
async function save() {{
  const enabled = [...document.querySelectorAll('.cb:checked')].map(c => c.value);
  document.getElementById('msg').textContent = 'Saving...';
  const r = await fetch('/admin/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN, enabled_ids: enabled}})}});
  const d = await r.json();
  document.getElementById('msg').textContent =
    r.ok ? `✅ Saved — ${{d.enabled}} ON, ${{d.disabled}} OFF` : '❌ Save failed';
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
