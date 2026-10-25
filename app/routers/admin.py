"""Local admin page - the 'who gets reminders' tick-boxes.

One page, no build tools: GET /admin?token=<agent_token> renders every
client with a checkbox (= clients.reminders_enabled, same flag the
STOP/START bot commands flip). Meant for the owner's son / the Tally
operator on the LAN - auth is the business agent_token.
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import json
import time as _time
from html import escape as _h
import logging
import re
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings
from app.db import require_db
from app.services import names
from app.services import wa_server
from app.services import ui_i18n as i18n
from app.jobs.reminder_sweep import OVERDUE_WINDOW_DAYS, STYLE_CADENCE
from app.models import (DEFAULT_CREDIT_DAYS, PLAN_LABELS, PLAN_LIMITS, Plan,
                        recommend_plan)

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def _warmup_banner(biz: dict) -> str:
    """While a new number is being warmed up, say so on the home page.

    reminder_sweep._daily_cap ramps a new shop 20, 40, 60 ... up to the plan's
    cap over its first week, because blasting a whole book from a number that
    has never sent bulk is the surest way to get it blocked. Nothing told the
    owner, so day one looks like a broken product: twenty messages out of five
    hundred parties. This is the missing sentence, and it disappears by itself
    once the ramp is done.
    """
    from app.jobs.reminder_sweep import _daily_cap
    try:
        created = _dt.date.fromisoformat(str(biz.get("created_at"))[:10])
    except (TypeError, ValueError):
        return ""
    today = _today()
    age = (today - created).days
    if age < 0 or age >= 7:
        return ""
    cap_now = _daily_cap(biz, today)
    full = _daily_cap({**biz, "created_at": (today - _dt.timedelta(days=99)).isoformat()}, today)
    if cap_now >= full:
        return ""                       # already at full speed; nothing to explain
    return (
        f'<div class="flash" style="display:block">'
        f'<b>Day {age + 1}: warming up your WhatsApp number.</b> '
        f'Today ASVA reminds up to {cap_now} customers, most overdue first, '
        f'building up to {full} a day. This is deliberate: a new number that '
        f'sends its whole list at once gets blocked by WhatsApp. '
        f'Nothing is lost, everyone is reached.</div>')


def _biz_by_token(token: str) -> dict:
    db = require_db()
    resp = (db.table("businesses")
            .select("id, business_name, weekly_off_day, blackout_dates, "
                    "reminder_style, reminder_custom_line, reminder_hour, msg_language, owner_language, "
                    "discount_pct, plan, upi_vpa, upi_vpa_2, upi_vpa_3, whatsapp_number, reminder_cadence, "
                    "bank_account_name, bank_account_no, bank_ifsc, bank_name, "
                    "overdue_repeat_days, overdue_max_repeats, reminder_batches, plan_expires_on, "
                    "catchup_date, catchup_action, wa_ready, wa_checked_at, setup_problem, "
                    "wa_transport, created_at")
            .eq("agent_token", token).limit(1).execute())
    if not resp.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return resp.data[0]


def _client_anchor(client: dict) -> _dt.date | None:
    """The party's selection-day anchor, same fallback chain as the sweep
    (reminder_anchor, else created_at)."""
    raw = client.get("reminder_anchor") or client.get("created_at")
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _client_points(biz: dict, bill: dict, credit_days: int, anchor: _dt.date | None = None):
    """Cadence points for one bill using the SAME engine and the SAME anchor
    logic the sweep uses, so what the dashboard shows is exactly what will
    actually send - never a diverging promise."""
    from app.jobs.reminder_sweep import cadence_points, DEFAULT_CADENCE
    try:
        inv = _dt.date.fromisoformat(str(bill["invoice_date"]))
    except (TypeError, ValueError, KeyError):
        return None, None
    due_str = bill.get("due_date")
    try:
        due = _dt.date.fromisoformat(str(due_str)) if due_str else inv
    except (TypeError, ValueError):
        due = inv
    overdue_from = (anchor - inv).days if (anchor and anchor > due) else None
    pts = cadence_points(
        cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
        repeat_days=biz.get("overdue_repeat_days") or 7,
        max_repeats=biz.get("overdue_max_repeats") or 3,
        credit_days=credit_days or 30,
        due_offset=(due - inv).days,
        overdue_from=overdue_from,
    )
    return inv, pts


def _past_the_ceiling(bills: list) -> bool:
    """Is any of this party's open bills past the last reminder ASVA will send?

    Reads the stored due date only. A bill with no due date we can parse is not
    counted, because this decides whether the owner is told to escalate, and
    that must never rest on a guessed date.
    """
    from app.jobs.reminder_sweep import needs_legal_escalation
    for b in bills:
        try:
            due = _dt.date.fromisoformat(str(b.get("due_date"))[:10])
        except (TypeError, ValueError):
            continue
        if needs_legal_escalation(due):
            return True
    return False


def _legal_fields(db, business_id: str) -> dict:
    """udyam_number + shop_address, read on their own and never fatally.

    They arrive in migration 062. Putting them in the main businesses select
    would mean a box that has not run the migration yet loses every admin page,
    not just the notice draft - so this is a separate, forgiving read.
    """
    try:
        r = (db.table("businesses").select("udyam_number, shop_address")
             .eq("id", business_id).limit(1).execute())
        return (r.data or [{}])[0] or {}
    except Exception:
        return {}


def _legal_state(bills: list, today: _dt.date | None = None) -> dict | None:
    """The party's legal state once the cadence has run out, or None.

    Sorting these parties by days overdue made every one of them look equally
    urgent. What actually has a deadline is the three-year limitation period,
    so the label comes from how much of that is left - see app/services/legal.py.
    """
    from app.services import legal
    return legal.party_legal(bills, today)


def _next_reminder(biz: dict, bills: list, credit_days: int, today: _dt.date,
                   anchor: _dt.date | None = None):
    """(label, colour) for a party's next scheduled reminder across its open
    bills. Green = an upcoming nudge; amber = an overdue-track reminder; the
    date is invoice_date + cadence-day. No messages-table lookup (dashboard is
    a hot path); the detail page shows the exact sent/pending breakdown."""
    best_date = None
    best_kind = None
    for b in bills:
        inv, pts = _client_points(biz, b, credit_days, anchor)
        if not pts:
            continue
        dsi = (today - inv).days
        # earliest cadence point still in the future (or exactly today)
        for day, kind in pts:
            if day >= dsi:
                d = inv + _dt.timedelta(days=day)
                if best_date is None or d < best_date:
                    best_date = d
                    best_kind = kind
                break
    if best_date is None:
        # No cadence point left. Either the debt has run the full 300 days past
        # due, in which case another WhatsApp message is not the instrument it
        # needs, or it simply has no due date we can reason about. Say which:
        # "all reminders done" on a year-old debt reads as "nothing to do".
        # Which of the four legal states it is decides the wording, because
        # "act before 12 Jan 2027" and "time barred, write off" are opposite
        # instructions and both used to read "escalation required".
        st = _legal_state(bills, today)
        if st:
            return st["label"], st["color"]
        return "All reminders done", "#999"
    kd = "Today" if best_date <= today else best_date.strftime("%d %b")
    color = "#c77b0a" if best_kind in ("overdue", "escalate") else "#0a7d33"
    tag = "overdue" if best_kind in ("overdue", "escalate") else "reminder"
    return f"{kd} ({tag})", color


def _chunked(items: list, size: int = 100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
RELOAD_COOLDOWN_MIN = 10   # "Reload data" may be pressed once per 10 minutes


def _today() -> _dt.date:
    """Today in the shop's timezone. Never date.today(): the server runs on UTC,
    so before 5:30 AM IST that is yesterday and a reminder due today renders as
    a future date ("22 Oct") on one row and "Today" on the next."""
    return _dt.datetime.now(IST).date()




def _parse_ts(raw) -> Optional[_dt.datetime]:
    """Parse a Postgres timestamptz string into an aware datetime."""
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    # Postgres may return microseconds beyond 6 digits or a +00 offset - trim.
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            return _dt.datetime.fromisoformat(s[:26] + s[-6:] if "+" in s[-6:] else s[:26])
        except ValueError:
            return None


def _last_synced_at(db, biz_id: str) -> Optional[_dt.datetime]:
    try:
        r = (db.table("tally_syncs").select("synced_at")
             .eq("business_id", biz_id).order("synced_at", desc=True)
             .limit(1).execute())
        if r.data:
            return _parse_ts(r.data[0]["synced_at"])
    except Exception:
        pass
    return None


def _fmt_ago(dt: Optional[_dt.datetime]) -> str:
    if not dt:
        return "abhi tak nahi"
    now = _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    mins = int((now - dt).total_seconds() // 60)
    if mins < 1:
        return "abhi abhi"
    if mins < 60:
        return f"{mins} min pehle"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} ghante pehle"
    return dt.astimezone(IST).strftime("%d %b, %H:%M")


def _tally_status(dt: Optional[_dt.datetime]) -> tuple[str, str]:
    """(label, colour) for the Tally-reachable dot. The agent stamps a sync only
    after it successfully reads Tally (every ~5 min), so sync freshness is a
    true reachability signal: fresh=connected, stale=slow, old/none=unreachable."""
    if not dt:
        return "Tally se abhi tak sync nahi", "#c0392b"
    now = _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    mins = (now - dt).total_seconds() / 60
    if mins <= 8:
        return "Tally connected", "#0a7d33"
    if mins <= 25:
        return "Tally sync ho raha hai...", "#c77b0a"
    return "Tally se contact nahi (Tally/laptop check karein)", "#c0392b"


# ── UI language (Hinglish default / English) ──────────────────────────────
# Pages render in Hinglish; when ?lang=en is passed we translate the FINAL
# rendered HTML (never the f-string templates - that keeps the fragile markup
# untouched and makes the worst-case failure "some text stays Hinglish", never
# a broken page). Applied longest-phrase-first so specifics beat generics.
_FEEDBACK = """
<style>
#asva-prog{position:fixed;left:0;top:0;height:2px;width:0;background:#0a7d33;
  z-index:99999;transition:width .4s ease;pointer-events:none;opacity:0}
#asva-prog.go{width:92%;opacity:1}
button:disabled{opacity:.65;cursor:progress}
button.asva-busy{position:relative;padding-right:26px}
button.asva-busy::after{content:"";position:absolute;right:8px;top:50%;width:10px;height:10px;
  margin-top:-5px;border:2px solid currentColor;border-right-color:transparent;
  border-radius:50%;animation:asva-spin .6s linear infinite}
@keyframes asva-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){button.asva-busy::after{animation-duration:2s}}
</style>
<script>
/* Slow is fine. Looking broken is not. See admin.py _FEEDBACK. */
(function(){
  if (window.__asvaFeedback) return; window.__asvaFeedback = 1;
  var bar = document.createElement('div'); bar.id = 'asva-prog';
  document.body.appendChild(bar);
  function go(){ requestAnimationFrame(function(){ bar.classList.add('go'); }); }
  addEventListener('beforeunload', go);
  addEventListener('pagehide', go);
  /* Only labels ASVA itself sets while waiting. A button the page disabled on
     purpose is left exactly as it is. */
  var BUSY = /^(\\.\\.\\.|Sending\\.\\.\\.|Saving\\.\\.\\.|Preparing\\.\\.\\.|Deleting\\.\\.\\.|Removing\\.\\.\\.|Checking\\.\\.\\.)$/;
  document.addEventListener('click', function(e){
    var t = e.target, b = (t && t.closest) ? t.closest('button') : null;
    if (!b || b.disabled) return;
    var label = b.textContent, html = b.innerHTML;
    setTimeout(function(){
      if (!b.isConnected || !b.disabled) return;
      b.classList.add('asva-busy');
      setTimeout(function(){
        if (!b.isConnected || !b.disabled) return;
        if (BUSY.test((b.textContent || '').trim())) {
          b.disabled = false; b.innerHTML = html; b.classList.remove('asva-busy');
          b.title = 'That did not go through. Check the connection and try again.';
        }
      }, 15000);
    }, 180);
  }, true);
})();
</script>
"""


def _ui_translate(html: str, lang: str) -> str:
    """Rewrite a Hinglish-authored admin page into the chosen locale
    (english / hinglish / hindi), then add the shared responsiveness layer."""
    # The founder kept the original green palette (the Ledger blue shipped in
    # 2.2.0 and was reverted on 2026-10-22); only the layout work stayed.
    out = i18n.translate_html(html, lang)
    # Injected AFTER translation: this text is English on purpose and must not
    # be rewritten. Appending works with or without a </body>.
    if "__asvaFeedback" in out:
        return out
    if "</body>" in out:
        return out.replace("</body>", _FEEDBACK + "</body>", 1)
    return out + _FEEDBACK


def _is_en(lang: str) -> bool:
    return i18n.is_en(lang)


# ── Shared chrome: favicon, top navigation, focus states ──────────────────
_FAVICON = ('<link rel="icon" href=\'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="%230a7d33"/>'
            '<text x="32" y="46" font-size="38" font-weight="bold" text-anchor="middle" '
            'fill="white" font-family="Arial">A</text></svg>\'>')

_NAV_CSS = """
 nav.topnav{display:flex;align-items:center;gap:2px;flex-wrap:wrap;background:#fff;border:1px solid #EAEAEA;border-radius:10px;padding:6px 10px;margin:0 0 18px}
 .topnav .brand{font-weight:800;letter-spacing:-0.02em;color:#0a7d33;padding:6px 12px 6px 6px;font-size:1.05rem}
 .topnav a{color:#555;text-decoration:none;padding:7px 12px;border-radius:7px;font-size:.92rem;font-weight:600}
 .topnav a:hover{background:#f4f4f1}
 .topnav a.on{background:#0a7d33;color:#fff}
 .topnav .sp{flex:1}
 .topnav a.lang{border:1px solid #EAEAEA;color:#1f6c9f;font-size:.85rem}
 .topnav a.lang.on{background:#0a7d33;color:#fff;border-color:#0a7d33}
 .topnav .ver{color:#b5b5b0;font-size:.72rem;padding:0 4px;align-self:center}
 /* Inside the desktop app the sidebar navigates and the app's own top bar
    carries Tally status + reload + language, so the whole in-page nav is
    hidden - no redundant strip on top of every page. */
 .in-app .topnav{display:none}
 .upbanner{background:#e1f3fe;border:1px solid #bfe2f7;color:#1f6c9f;border-radius:10px;padding:10px 14px;margin:0 0 14px;font-size:.92rem}
 :focus-visible{outline:2px solid #0a7d33;outline-offset:2px}
"""

_NAV_PAGES = [
    ("today", "Today", "/admin/today"),
    ("dashboard", "Dashboard", "/admin"),
    ("reminders", "Reminders", "/admin/reminders"),
    ("payments", "Payments", "/admin/payments"),
    ("analytics", "Analytics", "/admin/analytics"),
    ("accounts", "Accounts", "/admin/accounts"),
]


def _vparts(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v or "0").strip().split("."))
    except ValueError:
        return (0,)


def _together(**jobs):
    """Run independent read queries at the same time, return {name: result}.

    A page that makes five unrelated queries one after another pays five round
    trips; at ~250ms each that is most of its time. These handlers run in
    FastAPI's threadpool and the Supabase client's httpx session is
    thread-safe, so the page can cost the slowest query instead of the sum.
    An exception inside a job is raised here, exactly as it would have been.
    """
    if len(jobs) < 2:
        return {k: fn() for k, fn in jobs.items()}
    with _cf.ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futures.items()}


def _all_parties(db, business_id: str) -> list:
    """Every party the shop still has, oldest page first (1000 at a time).
    Archived parties are the ones removed from Tally; they stay hidden."""
    out: list = []
    start = 0
    while True:
        resp = (db.table("clients")
                .select("id, name, whatsapp_number, reminders_enabled, excluded, tally_ledger_name, "
                        "credit_days, reminder_batch, reminder_anchor, created_at")
                .eq("business_id", business_id)
                .is_("archived_at", "null")   # hide parties removed from Tally (reconcile)
                .order("name")
                .range(start, start + 999).execute())
        batch = resp.data or []
        out.extend(batch)
        if len(batch) < 1000:
            return out
        start += 1000


def _open_bill_rows(db, business_id: str) -> list:
    """Every bill with money still on it, paged."""
    out: list = []
    start = 0
    while True:
        resp = (db.table("bills")
                .select("client_id, outstanding, due_date, invoice_date")
                .eq("business_id", business_id)
                .in_("status", ["pending", "partial", "overdue"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        out.extend(batch)
        if len(batch) < 1000:
            return out
        start += 1000


def _messages_used(db, business_id: str, period: str) -> int:
    r = (db.table("usage").select("message_count")
         .eq("business_id", business_id).eq("period_month", period).limit(1).execute())
    return int(r.data[0]["message_count"]) if r.data else 0


# The release banner reads a table that changes when a version is published.
# Every shop was paying a round trip for it on every page view.
_RELEASE_MEMO: dict = {"at": 0.0, "html": ""}
_RELEASE_TTL = 120.0


def _update_banner(db) -> str:
    """Tally-style update notice: newest app_releases row vs this build.
    Best-effort - never breaks a page if the table is missing. Memoised for
    two minutes: a new version appears when one is published, not per page."""
    now = _time.monotonic()
    if now - _RELEASE_MEMO["at"] < _RELEASE_TTL:
        return _RELEASE_MEMO["html"]
    html = _build_update_banner(db)
    _RELEASE_MEMO.update(at=now, html=html)
    return html


def _build_update_banner(db) -> str:
    try:
        r = (db.table("app_releases").select("version, notes, mandatory")
             .order("created_at", desc=True).limit(1).execute()).data
        if not r:
            return ""
        latest = r[0]
        if _vparts(latest["version"]) <= _vparts(settings.app_version):
            return ""
        note = f' - {latest["notes"]}' if latest.get("notes") else ""
        strong = " <b>(zaroori update)</b>" if latest.get("mandatory") else ""
        return (f'<div class="upbanner">&#11014; Naya ASVA version <b>{latest["version"]}</b> '
                f'aa gaya hai{note}.{strong} '
                f'<a href="/download" style="color:#0a7d33;font-weight:800;text-decoration:underline">'
                f'Update download karein &rarr;</a></div>')
    except Exception:
        return ""


def _subscription_line(biz: dict) -> str:
    """One honest line about the plan period, colour-coded by state."""
    from app.services import subscription as subs
    exp = biz.get("plan_expires_on")
    if not exp:
        return ""
    status = subs.effective_status(exp)
    exp_fmt = str(exp)[:10]
    if status == "active":
        return (f'<div style="color:#346538;font-size:.85rem">'
                f'Subscription valid till <b>{exp_fmt}</b></div>')
    if status == "grace":
        return ('<div style="color:#956400;font-size:.85rem"><b>Subscription expired.</b> '
                f'Renew now or reminders will stop (expired {exp_fmt}).</div>')
    return ('<div style="color:#9f2f2d;font-size:.85rem"><b>Subscription suspended.</b> '
            'Reminders are OFF. Renew to resume instantly.</div>')


def _topnav(token: str, lang: str, active: str, self_url: str | None = None) -> str:
    """The one navigation bar every admin page shares: page links with the
    active page highlighted, plus a language switch. self_url overrides where
    the language switch lands (e.g. stay on the same party page)."""
    cur = i18n.norm(lang)
    q = f"token={token}&lang={cur}"
    links = "".join(
        f'<a class="pg {"on" if key == active else ""}" href="{path}?{q}">{label}</a>'
        for key, label, path in _NAV_PAGES)
    # self_url: path (+ its own params) WITHOUT token/lang - we append those.
    base = self_url or next((p for k, _, p in _NAV_PAGES if k == active), "/admin")
    sep = "&" if "?" in base else "?"
    # Three real choices, the active one highlighted - not a blind cycle toggle.
    langpills = "".join(
        f'<a class="lang{" on" if code == cur else ""}" '
        f'href="{base}{sep}token={token}&lang={code}">{i18n.NATIVE[code]}</a>'
        for code in i18n.LOCALES)
    # Inside the ASVA desktop app the LEFT sidebar already has these pages and
    # a language toggle - showing them twice confuses. The script tags the page
    # so CSS hides the duplicates; plain-browser use keeps the full bar.
    return (f'<script>if(navigator.userAgent.indexOf("Electron")>-1)'
            f'document.documentElement.classList.add("in-app")</script>'
            f'<nav class="topnav"><span class="brand">ASVA</span>{links}'
            f'<span class="sp"></span>'
            f'<a class="lang keep" href="#" onclick="location.reload();return false" '
            f'title="Page fresh karein">&#8635; Reload</a>'
            f'{langpills}'
            f'<span class="ver">v{settings.app_version}</span></nav>')


@router.get("/admin", response_class=HTMLResponse)
def admin_page(token: str = Query(...), lang: str = Query("english")):
    biz = _biz_by_token(token)
    db = require_db()

    today = _today()
    # Five unrelated reads. One after another they are most of this page's
    # time, so they go together and the page costs the slowest one.
    got = _together(
        synced=lambda: _last_synced_at(db, biz["id"]),
        clients=lambda: _all_parties(db, biz["id"]),
        bills=lambda: _open_bill_rows(db, biz["id"]),
        used=lambda: _messages_used(db, biz["id"], _today().replace(day=1).isoformat()),
        banner=lambda: _update_banner(db),
    )
    synced_dt = got["synced"]
    synced_label = _fmt_ago(synced_dt)
    synced_iso = synced_dt.isoformat() if synced_dt else ""
    tally_label, tally_color = _tally_status(synced_dt)
    clients: list = got["clients"]
    update_banner_html = got["banner"]

    # Shown instead of "All clear": a shop can be online, syncing, and still be
    # importing zero customers (see tally._flag_setup_problem).
    # json.dumps (NOT repr): Python's repr of a bool is "True", which is an
    # undefined identifier in JS and would throw before any of the page's script
    # runs - a blank dashboard for every shop.
    setup_problem_js = json.dumps((biz.get("setup_problem") or "").strip())
    has_parties_js = json.dumps(bool(clients))

    totals: dict[str, Decimal] = {}
    overdue_days: dict[str, int] = {}   # client_id -> max days past due
    bills_by_client: dict[str, list] = {}
    for b in got["bills"]:
        cid = b["client_id"]
        totals[cid] = totals.get(cid, Decimal(0)) + Decimal(str(b["outstanding"]))
        bills_by_client.setdefault(cid, []).append(b)
        dd = b.get("due_date")
        if dd:
            try:
                od = (today - _dt.date.fromisoformat(str(dd))).days
                if od > 0:
                    overdue_days[cid] = max(overdue_days.get(cid, 0), od)
            except (TypeError, ValueError):
                pass

    clients.sort(key=lambda c: totals.get(c["id"], Decimal(0)), reverse=True)

    from app.services.batches import get_batches
    batches = get_batches(biz)
    multi_batch = len(batches) > 1

    def _batch_lbl(b: dict) -> str:
        lang = "English" if b.get("lang") == "english" else "Hinglish"
        try:
            hh = f'{int(b.get("hour", 11)):02d}:00'
        except (TypeError, ValueError):
            hh = "11:00"
        return f'{lang} {hh}'

    # Assignment used to need four controls that fought each other: a dropdown
    # on every row, a second dropdown in the toolbar, an Apply button, and the
    # Reminder? checkbox doing double duty as "which rows does Apply mean".
    # Now there is ONE control. The chip in the Batch column IS the button; it
    # opens a list of batches and saves the moment one is picked. The same list
    # opened from the column header assigns every row currently shown, which is
    # what the search box and the tabs are already for.
    batch_meta = json.dumps([
        {"i": i, "name": (b.get("name") or f"Batch {i + 1}"), "lbl": _batch_lbl(b)}
        for i, b in enumerate(batches)])
    # Five tints so the Batch column can be read down the page at a glance.
    BTINT = ["#eaf7ee", "#e1f3fe", "#fbf3db", "#fdebec", "#eee9fb"]
    BINK = ["#0a5d28", "#1f6c9f", "#8a6100", "#9f2f2d", "#54399c"]

    def _bchip(cid: str, cur: int) -> str:
        nm = (batches[cur].get("name") or f"Batch {cur + 1}")
        nm = nm.replace("&", "&amp;").replace("<", "&lt;")
        return (f'<button class="bchip" data-cid="{cid}" data-b="{cur}" '
                f'style="background:{BTINT[cur % 5]};color:{BINK[cur % 5]}" '
                f'title="Click to change the batch for this party">'
                f'<b>{cur + 1}</b> {nm}</button>')

    rows = []
    for c in clients:
        out = totals.get(c["id"], Decimal(0))
        out_str = f"₹{_inr_digits(out)}" if out else "-"
        od = overdue_days.get(c["id"], 0)
        od_str = f"{od} din" if od else "-"
        # Phone is a click-to-edit button: parties whose number was never stored
        # in Tally can have it added right here (India +91 applied on save).
        num_raw = c.get("whatsapp_number") or ""
        num_disp = num_raw[-10:] if len(num_raw) >= 10 else num_raw
        _add_lbl = "+ Add number" if lang == "english" else "+ Number daalein"
        _ph_cls = "phbtn" if num_raw else "phbtn add"
        phone = (f'<button class="{_ph_cls}" data-cid="{c["id"]}" data-num="{num_raw}">'
                 + (num_disp if num_raw else _add_lbl) + '</button>')
        rem_on = c.get("reminders_enabled", True)
        checked = "checked" if rem_on else ""
        # Source tab: excluded parties live in their own Do-not-chase tab; the
        # rest split into Tally-synced (has a ledger name) vs OCR/manual (non-Tally).
        is_excl = bool(c.get("excluded"))
        # home = the tab this party belongs to when it is NOT on the
        # do-not-chase list. Carried in the markup so bringing one back can
        # put the row where it came from without re-rendering the page.
        home = "tally" if (c.get("tally_ledger_name") or "").strip() else "nontally"
        src = "exclude" if is_excl else home
        cname = (c["name"] or "").replace("&", "&amp;").replace("<", "&lt;")
        nm_attr = cname.replace('"', "&quot;")
        # Non-Tally parties get manual controls: "₹ Pay" records a payment and
        # "Bills" opens the party page where bills are added/edited/deleted.
        pay_btn = ""
        if src == "nontally":
            pay_btn = (
                f'<button class="paybtn" data-cid="{c["id"]}" data-party="{nm_attr}">₹ Pay</button> '
                f'<a class="nbills" href="/admin/party?token={token}&client_id={c["id"]}&lang={lang}" '
                f'title="Bill add, edit ya delete karein">Bills</a>')
        cd = c.get("credit_days")
        cd_val = int(cd) if cd else 0
        cd_label = f"{cd_val}d" if cd_val else "set"
        # Reminder status badge: Off, no-bills dash, or next scheduled send.
        cbills = bills_by_client.get(c["id"], [])
        if not rem_on:
            rem_badge = '<span class="rbadge off">OFF</span>'
        elif not cbills:
            rem_badge = '<span class="rbadge none">-</span>'
        else:
            rl, rc = _next_reminder(biz, cbills, cd_val, today, _client_anchor(c))
            rem_badge = f'<span class="rbadge" style="color:{rc};border-color:{rc}">{rl}</span>'
        # Days of limitation runway left on this party's soonest-expiring open
        # bill. Rows that are still inside the cadence sort last - they have no
        # decision pending.
        _lgs = _legal_state(cbills, today) if cbills else None
        if _lgs and _lgs.get("deadline"):
            rw = (_lgs["deadline"] - today).days
        elif _lgs and _lgs["state"] == "time_barred":
            rw = -1
        elif _lgs:
            rw = 9000
        else:
            rw = 99999
        cur_batch = int(c.get("reminder_batch") or 0)
        if cur_batch >= len(batches):
            cur_batch = 0
        batch_cell = (_bchip(c["id"], cur_batch) if multi_batch
                      else f'<span class="rbadge none">{cur_batch + 1}</span>')
        _xs = "border-radius:5px;padding:4px 9px;font-size:.85em;cursor:pointer"
        if is_excl:
            actions = (f'<button class="exclbtn" data-cid="{c["id"]}" data-party="{nm_attr}" '
                       f'data-exclude="0" style="background:#eaf7ee;color:#0a7d33;border:1px solid #a9d8b8;{_xs}">'
                       f'Bring back</button>')
        else:
            actions = (f'<button class="sendbtn" data-party="{nm_attr}">Send now</button> {pay_btn} '
                       f'<button class="exclbtn" data-cid="{c["id"]}" data-party="{nm_attr}" '
                       f'data-exclude="1" title="Move to the do-not-chase list" '
                       f'style="background:#fbf3db;color:#8a6100;border:1px solid #e2c46a;{_xs}">Exclude</button>')
        rows.append(
            f'<tr data-name="{cname.lower()}" data-amt="{float(out)}" data-od="{od}" '
            f'data-rw="{rw}" data-src="{src}" data-home="{home}">'
            f'<td class="c-rem" data-label="Reminder?"><input type="checkbox" class="cb" value="{c["id"]}" {checked}></td>'
            f'<td class="c-party"><a class="plink" href="/admin/party?token={token}&client_id={c["id"]}&lang={lang}">{cname}</a></td>'
            f'<td class="amt" data-label="Baaki">{out_str}</td>'
            f'<td class="od" data-label="Overdue">{od_str}</td>'
            f'<td data-label="Agla reminder">{rem_badge}</td>'
            f'<td data-label="Batch">{batch_cell}</td>'
            f'<td data-label="Credit days"><button class="termbtn" data-cid="{c["id"]}" data-party="{nm_attr}" data-cd="{cd_val}">{cd_label}</button></td>'
            f'<td class="ph" data-label="WhatsApp">{phone}</td>'
            f'<td class="c-act" data-label="Actions">{actions}</td></tr>'
        )

    # Reminder style only drives the per-party schedule preview here; the
    # actual settings live on the Reminders tab.
    style = (biz.get("reminder_style") or "standard")

    # ── Plan usage (this month) ────────────────────────────────────────
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
    used = got["used"]

    exclude_n = sum(1 for c in clients if c.get("excluded"))
    _active = [c for c in clients if not c.get("excluded")]
    tally_n = sum(1 for c in _active if (c.get("tally_ledger_name") or "").strip())
    nontally_n = len(_active) - tally_n

    # A plain, at-a-glance count line (no plan upsell): how many parties ASVA is
    # tracking, how many owe money right now, and the total outstanding.
    parties_n = len(_active)
    owing_n = sum(1 for c in _active if totals.get(c["id"], Decimal(0)) > 0)
    total_out = sum((totals.get(c["id"], Decimal(0)) for c in _active), Decimal(0))

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

    # Base cadence for the per-party schedule preview (truthful to what sends).
    base_cadence_json = json.dumps(STYLE_CADENCE.get(style, STYLE_CADENCE["standard"]))

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{biz['business_name']} - Dashboard</title>{_FAVICON}
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:16px auto;max-width:1440px;padding:0 14px;background:#F7F6F3;color:#2F3437}}
 h2{{margin:0 0 4px;letter-spacing:-0.02em}} .sub{{color:#787774;margin-bottom:12px;font-size:.95em}}
 button{{font:inherit;padding:8px 14px;cursor:pointer;border:1px solid #EAEAEA;background:#fff;border-radius:8px;transition:background 150ms ease,transform 120ms ease-out}}
 button:hover{{background:#f4f4f1}}
 button:active{{transform:scale(.97)}}
 #save{{background:#0a7d33;color:#fff;border:0}}
 #save:hover{{background:#086b2b}}
 #msg{{color:#0a7d33;font-weight:600;margin-left:6px}}
 .bar{{position:sticky;top:0;background:#F7F6F3;padding:10px 0;display:flex;gap:8px;align-items:center;z-index:2;flex-wrap:wrap}}
 input[type=search]{{flex:1;padding:9px 12px;font-size:1em;min-width:140px;border:1px solid #EAEAEA;border-radius:8px;background:#fff}}
 #sort{{padding:9px;font-size:.95em;border:1px solid #EAEAEA;border-radius:8px;background:#fff}}
 .tablewrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;background:#fff;border:1px solid #EAEAEA;border-radius:12px}}
 table{{border-collapse:collapse;width:100%;min-width:760px}}
 td,th{{padding:9px 12px;border-bottom:1px solid #EAEAEA;text-align:left;font-size:.93em}}
 th{{background:#fff;font-size:.76em;color:#787774;text-transform:uppercase;letter-spacing:.04em}}
 tr:last-child td{{border-bottom:0}}
 tbody tr:hover td,tr:hover td{{background:#f7faf7}}
 input[type=checkbox].cb{{width:17px;height:17px;cursor:pointer}}
 .amt{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
 .od{{text-align:right;color:#9f2f2d;font-size:.9em;white-space:nowrap;font-variant-numeric:tabular-nums}}
 .ph{{color:#787774;font-size:.9em}}
 .nono{{color:#c0392b;font-size:.95em}}
 .phbtn{{background:#eef3ec;border:1px solid #d7e3d7;border-radius:6px;padding:4px 9px;font-size:.9em;cursor:pointer;color:#0a5d28}}
 .phbtn:hover{{background:#e1efe1}}
 .phbtn.add{{background:#fdecea;border-color:#e6b3ac;color:#c0392b;font-weight:600}}
 .row{{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}}
 .hint{{color:#787774;font-size:.85em;margin-top:6px}}
 .usage{{margin:12px 0;padding:14px 16px;border:1px solid #EAEAEA;border-radius:12px;background:#fff;font-size:.95em}}
 .ubar{{height:8px;background:#EDEDEA;border-radius:5px;margin-top:6px;overflow:hidden}}
 .ufill{{height:100%;background:#0a7d33}}
 .urec{{margin-top:8px;font-size:.92em;color:#346538}}
 .urec.warn{{color:#9f2f2d;font-weight:600}}
 .urec.ok{{color:#346538}}
 .umsg{{margin-top:4px;font-size:.8em;color:#b5b5b0}}
 .count{{margin:10px 0 2px;font-size:1.02em;color:#2F3437}} .count b{{color:#0a7d33;font-variant-numeric:tabular-nums}}
 .subtabs{{display:inline-flex;border:1px solid #EAEAEA;border-radius:8px;overflow:hidden;background:#fff;margin:14px 0 6px}}
 .subtabs button{{border:0;border-radius:0;background:#fff;padding:9px 18px;font-weight:600;color:#787774}}
 .subtabs button+button{{border-left:1px solid #EAEAEA}}
 .subtabs button.on{{background:#0a7d33;color:#fff}}
 .addparty{{margin:8px 0 6px;border:1px solid #0a7d33;background:#eef7ef;color:#0a7d33;font-weight:600;border-radius:8px;padding:8px 14px;cursor:pointer}}
 .addparty:hover{{background:#e1f0e3}}
 .sendbtn{{padding:5px 10px;font-size:.85em;border:1px solid #0a7d33;color:#0a7d33;background:#fff}}
 .sendbtn:disabled{{opacity:.5;cursor:default}}
 .paybtn{{padding:5px 10px;font-size:.85em;border:1px solid #7d5a0a;color:#7d5a0a;background:#fff}}
 .paybtn:disabled{{opacity:.5;cursor:default}}
 .nbills{{display:inline-block;padding:5px 10px;font-size:.85em;border:1px solid #1f6c9f;color:#1f6c9f;background:#fff;border-radius:8px;text-decoration:none;white-space:nowrap}}
 .nbills:hover{{background:#e1f3fe}}
 .termbtn{{padding:4px 10px;font-size:.85em}}
 .plink{{color:#1f6c9f;text-decoration:none;font-weight:600}}
 .plink:hover{{text-decoration:underline}}
 .bchip{{border:1px solid rgba(0,0,0,.10);border-radius:999px;padding:4px 11px;font-size:.84em;
   cursor:pointer;white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis;font:inherit;
   font-size:.84em;line-height:1.3}}
 .bchip b{{font-weight:800;margin-right:4px}}
 .bchip:hover{{filter:brightness(.96);border-color:rgba(0,0,0,.22)}}
 .bchip.saving{{opacity:.5;cursor:default}}
 .bhead{{background:#fff;border:1px solid #d7ddd7;border-radius:999px;color:#1f6c9f;
   font-size:.82em;padding:2px 9px;margin-left:6px;cursor:pointer;text-transform:none;letter-spacing:0}}
 .bhead:hover{{background:#f1f6f1}}
 /* The batch picker: one list, opened from a row chip or from the column head. */
 .bpop{{position:absolute;z-index:12;display:none;background:#fff;border:1px solid #e3e7e2;
   border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.14);min-width:270px;max-width:320px;overflow:hidden}}
 .bpop.show{{display:block}}
 .bpophd{{padding:10px 14px;font-size:.8em;color:#787774;border-bottom:1px solid #f0f0ee;background:#fbfbfa}}
 .bpopitem{{position:relative;display:block;width:100%;text-align:left;background:#fff;border:0;
   border-bottom:1px solid #f4f4f2;padding:11px 30px 11px 14px;cursor:pointer;font:inherit;
   font-size:.92em;color:#2F3437}}
 .bpopitem:hover{{background:#f4f9f4}}
 .bpopitem .bl{{display:block;font-size:.82em;color:#787774;margin-top:2px}}
 .bpopitem.cur{{background:#f1f8f3}}
 .bpopitem.cur::after{{content:'✓';position:absolute;right:12px;top:50%;
   transform:translateY(-50%);color:#0a7d33;font-weight:700}}
 .rbadge{{display:inline-block;font-size:.8em;border:1px solid #ccc;border-radius:12px;padding:2px 9px;white-space:nowrap}}
 .rbadge.off{{color:#999;border-color:#ddd;background:#f5f5f5}}
 .rbadge.none{{color:#bbb;border:0}}
 .modal{{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:9}}
 .modal.show{{display:flex}}
 .modalbox{{background:#fff;max-width:420px;width:90%;border-radius:14px;padding:18px 20px}}
 .modalbox h3{{margin:0 0 10px}}
 .msgprev{{white-space:pre-wrap;background:#f6f6f4;border:1px solid #EAEAEA;border-radius:8px;padding:12px;font-size:.95em;line-height:1.5;max-height:50vh;overflow:auto}}
 .catchband{{display:none;background:#fbf3db;border:1px solid #f0dfa8;color:#956400;border-radius:12px;padding:12px 16px;margin:0 0 14px;font-size:.95em;line-height:1.7}}
 .cbsend{{background:#0a7d33;color:#fff;border:0;margin-left:10px;padding:7px 16px}}
 .cbsend:hover{{background:#086b2b}}
 .cbskip{{margin-left:8px;padding:7px 14px}}
 .skipbtn{{padding:2px 9px;font-size:.8em;margin-left:6px}}
 /* P2: on a narrow window (small shop laptop / phone browser) the 9-column table
    would push WhatsApp + Actions off-screen behind a scrollbar the owner never
    finds. Below 760px each row becomes a self-contained CARD - name on top, every
    field labelled, the WhatsApp + Actions always visible. No horizontal scroll. */
 @media (max-width:760px){{
   #ptable{{min-width:0;display:block}}
   #ptable tr:not([data-name]){{display:none}}      /* the header row */
   #ptable tr[data-name]{{display:block;border:1px solid #EAEAEA;border-radius:12px;background:#fff;margin:10px 0;padding:6px 14px}}
   #ptable td{{display:flex;align-items:center;justify-content:space-between;gap:14px;border:0;border-bottom:1px solid #F2F1EE;padding:9px 0;text-align:right;font-size:.95em}}
   #ptable tr[data-name] td:last-child{{border-bottom:0}}
   #ptable td::before{{content:attr(data-label);color:#787774;font-size:.8em;font-weight:600;text-transform:uppercase;letter-spacing:.03em;text-align:left;flex:0 0 auto}}
   #ptable td.c-party{{justify-content:flex-start;font-size:1.08em;font-weight:700;padding:4px 0 10px}}
   #ptable td.c-party::before{{display:none}}
   #ptable td.c-party .plink{{color:#2F3437}}
   #ptable td.c-rem{{flex-direction:row-reverse;justify-content:flex-end;gap:10px}}
   #ptable td.c-act{{flex-wrap:wrap;justify-content:flex-start;gap:8px}}
   #ptable td.c-act::before{{flex-basis:100%}}
   .bar{{position:static}}
 }}
{_NAV_CSS}
</style></head><body>
{_topnav(token, lang, "dashboard")}
{update_banner_html}
<div class="catchband" id="catchband"></div>
<h2>{biz['business_name']}</h2>
<div class="sub">Tick = us party ko reminder ON. Batch, timing aur holidays: <b>Reminders</b> tab.</div>
{_subscription_line(biz)}

<div class="count"><b>{parties_n:,}</b> parties &middot; <b>{owing_n:,}</b> owe money now &middot; <b>&#8377;{_inr_digits(total_out)}</b> outstanding</div>

<div id="todaycard" style="display:none;margin:10px 0 6px;border:1px solid #EAEAEA;border-radius:10px;padding:12px 14px;background:#fbfbfa"></div>

<div class="subtabs">
 <button class="on" data-sub="tally">Tally bills ({tally_n:,})</button>
 <button data-sub="nontally">Non-Tally bills ({nontally_n:,})</button>
 <button data-sub="exclude">Do-not-chase ({exclude_n:,})</button>
</div>
<button class="addparty" onclick="openAddParty()" title="Add a customer you track in ASVA only, not in Tally">+ Add non-Tally party</button>
<button class="addparty" onclick="openImportNumbers()" title="Fill missing WhatsApp numbers by pasting contacts from your phone">Import numbers from phone</button>

<div class="bar">
 <input type="search" id="q" placeholder="Party dhundo...">
 <select id="sort" onchange="sortRows()">
  <option value="amt">Baaki: zyada pehle</option>
  <option value="od">Overdue: zyada din pehle</option>
  <option value="name">Naam: A to Z</option>
  <option value="name_owe">Naam: A to Z (baaki wale)</option>
  <option value="legal">Needs a decision: running out of time first</option>
 </select>
 <button onclick="setAll(true)" title="Sabko reminder ON karo">Sab ON</button>
 <button onclick="setAll(false)" title="Sabko reminder OFF karo">Sab OFF</button>
 <button id="save" onclick="save()">Save list</button>
 <button onclick="dueToday()" title="Aaj kaun ko reminder jayega dekhein">Aaj kaun?</button>
 <span id="msg"></span>
</div>
<div class="bpop" id="bpop"><div class="bpophd" id="bpophd"></div><div id="bpoplist"></div></div>
<div class="tablewrap">
<table id="ptable"><tr><th>Reminder?</th><th>Party</th><th>Baaki</th><th>Overdue</th><th>Agla reminder</th><th>Batch{' <button class="bhead" onclick="event.stopPropagation();openBatchPop(null,this)" title="Put every party shown below into one batch">all shown</button>' if multi_batch else ''}</th><th>Credit days</th><th>WhatsApp</th><th>Actions</th></tr>
{''.join(rows)}
</table>
</div>

<div class="modal" id="duemodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Aaj kaun ko reminder jayega</h3>
    <div id="duebody" class="msgprev">...</div>
    <div style="margin-top:12px;text-align:right"><button onclick="document.getElementById('duemodal').classList.remove('show')">Close</button></div>
  </div>
</div>

<div class="modal" id="newpartymodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Add a non-Tally party</h3>
    <div class="hint" style="margin:0 0 6px">A customer you track in ASVA only (not in Tally). Same reminders and payment handling.</div>
    <label style="font-weight:600;display:block;margin:10px 0 5px">Party name</label>
    <input id="nb_name" type="text" maxlength="120" placeholder="Ramesh Traders"
      style="width:100%;padding:11px 12px;font-size:1em;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box">
    <label style="font-weight:600;display:block;margin:12px 0 5px">WhatsApp number (10 digits, optional)</label>
    <input id="nb_phone" type="text" inputmode="numeric" placeholder="9876543210"
      style="width:100%;padding:11px 12px;font-size:1em;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box">
    <div style="margin-top:18px;text-align:right">
      <button onclick="document.getElementById('newpartymodal').classList.remove('show')">Cancel</button>
      <button id="nb_save" style="background:#0a7d33;color:#fff;border:0">Create</button>
    </div>
  </div>
</div>

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
      <button id="termsave" onclick="saveTerms()" style="background:#0a7d33;color:#fff;border:0">Save</button>
    </div>
  </div>
</div>

<div class="modal" id="paymodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Record payment</h3>
    <div id="payparty" style="font-weight:700;margin-bottom:10px"></div>
    <label style="font-weight:600;display:block;margin-bottom:5px">Amount received (Rs)</label>
    <input id="payamt" type="text" inputmode="numeric" placeholder="10000"
      style="width:100%;padding:11px 12px;font-size:1em;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box">
    <div style="margin-top:18px;text-align:right">
      <button onclick="document.getElementById('paymodal').classList.remove('show')">Cancel</button>
      <button id="paysave" style="background:#0a7d33;color:#fff;border:0">Save</button>
    </div>
  </div>
</div>

<div class="modal" id="nummodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Customer WhatsApp number</h3>
    <div id="numparty" style="font-weight:700;margin-bottom:10px"></div>
    <label style="font-weight:600;display:block;margin-bottom:5px">10-digit mobile number</label>
    <input id="numinput" type="text" inputmode="numeric" maxlength="14" placeholder="9876543210"
      style="width:100%;padding:11px 12px;font-size:1em;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box">
    <div style="margin-top:8px;font-size:.82em;color:#787774">Just type the number. +91 is added automatically. No spaces or +.</div>
    <div style="margin-top:18px;text-align:right">
      <button onclick="document.getElementById('nummodal').classList.remove('show')">Cancel</button>
      <button id="numsave" style="background:#0a7d33;color:#fff;border:0">Save</button>
    </div>
  </div>
</div>

<div class="modal" id="importnummodal" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="modalbox">
    <h3>Import numbers from your phone</h3>
    <div style="font-size:.85em;color:#787774;margin-bottom:8px">Paste your contacts (name and number, one per line), or paste a contact file (.vcf) you shared from your phone. ASVA matches each to a Tally party and fills the ones missing a number. Numbers you already have are never changed.</div>
    <textarea id="imp_text" rows="8" placeholder="Ramesh Traders  9876543210&#10;Anand Electricals, 9876500002" style="width:100%;padding:11px 12px;font-size:.95em;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box"></textarea>
    <div id="imp_result" style="margin-top:10px;font-size:.88em;line-height:1.5"></div>
    <div style="margin-top:16px;text-align:right">
      <button onclick="document.getElementById('importnummodal').classList.remove('show')">Close</button>
      <button id="imp_go" style="background:#0a7d33;color:#fff;border:0">Fill numbers</button>
    </div>
  </div>
</div>

<script>
const TOKEN = {token!r};

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

// A shop can be 'connected' and still be importing nothing (e.g. its Tally
// groups customers under a renamed group). Never let that read as 'All clear'.
const SETUP_PROBLEM = {setup_problem_js};
const HAS_PARTIES = {has_parties_js};
const LANG = {lang!r};

// Sync time + Tally status + Reload live in the app's top bar now (global).
// Here we only auto re-render when the 5-min auto-sync brings new data - and
// never while the owner is mid-edit (their next Save/Reload picks it up).
const SYNCED_AT = {synced_iso!r};   // ISO of last sync at page load (or "")
let DIRTY = false;
// window.__ASVA_DIRTY__ is read by the desktop shell before it reloads this
// webview on a background Tally sync. "Busy" = the owner is mid-task and a reload
// would yank the ground from under them: either ticked-but-unsaved reminder boxes
// (DIRTY), OR an active search (typed text or the search box focused). While busy,
// the shell skips reloading this page, so a 5-minute sync never wipes a selection
// or throws them back to the top of an un-searched list.
function _searchActive() {{
  var q = document.getElementById('q');
  return !!(q && (q.value || document.activeElement === q));
}}
function updateBusy() {{
  try {{ window.__ASVA_DIRTY__ = DIRTY || _searchActive() || _asvaBusy(); }} catch (e) {{}}
}}
function markDirty() {{ DIRTY = true; updateBusy(); }}
setInterval(async () => {{
  try {{
    const r = await fetch('/admin/sync-status?token=' + encodeURIComponent(TOKEN));
    const s = await r.json();
    if (s.last_synced_at && s.last_synced_at !== SYNCED_AT && !DIRTY && !_searchActive()) location.reload();
  }} catch (e) {{}}
}}, 90000);

let SRC = 'tally';   // Dashboard opens on Tally bills
let OWING_ONLY = false;   // "A to Z (owing only)" hides parties with nothing due
function applyFilter() {{
  const q = document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    const okq = r.dataset.name.includes(q);
    const oks = SRC === 'all' || r.dataset.src === SRC;
    const okOwe = !OWING_ONLY || parseFloat(r.dataset.amt || '0') > 0;
    r.style.display = (okq && oks && okOwe) ? '' : 'none';
  }});
}}
document.getElementById('q').addEventListener('input', () => {{ applyFilter(); updateBusy(); }});
document.getElementById('q').addEventListener('focus', updateBusy);
document.getElementById('q').addEventListener('blur', updateBusy);
function setAll(v) {{
  markDirty();
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    if (r.style.display !== 'none') r.querySelector('.cb').checked = v;
  }});
}}
document.querySelectorAll('.cb').forEach(c => c.addEventListener('change', markDirty));
function sortRows() {{
  const key = document.getElementById('sort').value;
  OWING_ONLY = (key === 'name_owe');   // this option also filters to debtors
  const rows = [...document.querySelectorAll('#ptable tr[data-name]')];
  rows.sort((a, b) => {{
    if (key === 'legal') return parseFloat(a.dataset.rw || 99999) - parseFloat(b.dataset.rw || 99999);
    if (key === 'name' || key === 'name_owe') return a.dataset.name.localeCompare(b.dataset.name);
    if (key === 'od') return parseFloat(b.dataset.od) - parseFloat(a.dataset.od);
    return parseFloat(b.dataset.amt) - parseFloat(a.dataset.amt);
  }});
  const tb = rows.length ? rows[0].parentNode : null;
  if (tb) rows.forEach(r => tb.appendChild(r));
  applyFilter();   // re-apply so the owing-only filter takes/releases effect
}}
async function save() {{
  const enabled = [...document.querySelectorAll('.cb:checked')].map(c => c.value);
  document.getElementById('msg').textContent = 'Saving...';
  const r = await fetch('/admin/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN, enabled_ids: enabled}})}});
  const d = await r.json();
  document.getElementById('msg').textContent =
    r.ok ? `✓ Saved - ${{d.enabled}} ON, ${{d.disabled}} OFF` : 'Save failed';
  if (r.ok) {{ DIRTY = false; updateBusy(); }}
}}

// sub-tabs filter the party table: Tally vs Non-Tally
document.querySelectorAll('.subtabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.subtabs button').forEach(x => x.classList.toggle('on', x === b));
  SRC = b.dataset.sub;
  applyFilter();
}});
applyFilter();   // apply the default Tally filter on load

// Exclude / bring back: flip the party's do-not-chase flag and MOVE THE ROW.
// Reloading instead meant re-rendering nine hundred rows to move one of them,
// which cost about two seconds and threw away the scroll position and the
// search box. The row knows which tab it belongs to (data-home).
function recountTabs() {{
  const n = {{tally: 0, nontally: 0, exclude: 0}};
  document.querySelectorAll('tr[data-src]').forEach(r => {{
    if (n[r.dataset.src] !== undefined) n[r.dataset.src]++;
  }});
  const label = {{tally: 'Tally bills', nontally: 'Non-Tally bills', exclude: 'Do-not-chase'}};
  document.querySelectorAll('.subtabs button[data-sub]').forEach(b => {{
    const k = b.dataset.sub;
    if (label[k]) b.textContent = label[k] + ' (' + n[k].toLocaleString('en-IN') + ')';
  }});
}}
async function toggleExclude(btn) {{
  const cid = btn.dataset.cid, exclude = btn.dataset.exclude === '1', party = btn.dataset.party;
  const ask = exclude
    ? party + ' ko do-not-chase list me daalein? Reminders band ho jayenge.'
    : party + ' ko wapas laayein? Woh phir se chase list me aa jayega.';
  if (!confirm(ask)) return;
  const row = btn.closest('tr');
  const wasSrc = row ? row.dataset.src : null;
  btn.disabled = true; btn.textContent = '...';
  let ok = false;
  try {{
    const r = await fetch('/admin/set-excluded', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: cid, excluded: exclude}})}});
    ok = r.ok;
  }} catch (e) {{ ok = false; }}
  btn.disabled = false;
  if (!ok) {{
    if (row && wasSrc) row.dataset.src = wasSrc;
    btn.textContent = exclude ? 'Exclude' : 'Bring back';
    alert('That did not go through. Check the connection and try again.');
    return;
  }}
  if (row) row.dataset.src = exclude ? 'exclude' : (row.dataset.home || 'tally');
  btn.dataset.exclude = exclude ? '0' : '1';
  btn.textContent = exclude ? 'Bring back' : 'Exclude';
  const cb = row && row.querySelector('input.cb');
  if (cb) cb.checked = !exclude;        // an excluded party is never chased
  recountTabs();
  applyFilter();                         // the row leaves the tab you are on
}}
document.querySelectorAll('.exclbtn').forEach(b => b.onclick = () => toggleExclude(b));

async function sendNow(btn) {{
  const party = btn.dataset.party;
  if (!confirm('Abhi ' + party + ' ko reminder bhejein?')) return;
  btn.disabled = true; btn.textContent = 'Sending...';
  try {{
    const r = await fetch('/admin/send-now', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, party: party}})}});
    const d = await r.json();
    btn.textContent = (r.ok && d.sent) ? '✓ Sent' : '✗ ' + (d.detail || 'Failed');
    if (d.wa_down) alert(d.detail);   // strong, unmissable when WhatsApp is off
  }} catch (e) {{ btn.textContent = '✗ Failed'; }}
  setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Send now'; }}, 4000);
}}
document.querySelectorAll('.sendbtn').forEach(b => b.onclick = () => sendNow(b));

// Record payment via an in-page modal (prompt() does not work in the app).
let PAY_CID = null;
function recordPayment(btn) {{
  PAY_CID = btn.dataset.cid;
  document.getElementById('payparty').textContent = btn.dataset.party || '';
  document.getElementById('payamt').value = '';
  document.getElementById('paymodal').classList.add('show');
  setTimeout(() => document.getElementById('payamt').focus(), 40);
}}
async function savePayment() {{
  const n = parseFloat(String(document.getElementById('payamt').value).replace(/[,₹\\s]/g, ''));
  if (!(n > 0)) {{ alert('Enter a valid amount, e.g. 10000'); return; }}
  const btn = document.getElementById('paysave'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/record-payment', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: PAY_CID, amount: n}})}});
    const d = await r.json();
    if (r.ok && d.applied > 0) {{ location.reload(); return; }}
    btn.disabled = false; btn.textContent = 'Save';
    alert(d.detail || 'Nothing was applied.');
  }} catch (e) {{ btn.disabled = false; btn.textContent = 'Save'; alert('Could not save. Please try again.'); }}
}}
document.getElementById('paysave').onclick = savePayment;
document.querySelectorAll('.paybtn').forEach(b => b.onclick = () => recordPayment(b));

// Add / edit a customer's WhatsApp number in ASVA (for parties whose number was
// never stored in Tally). +91 is added on the server; the owner types 10 digits.
let NUM_CID = null;
function openNumberModal(btn) {{
  NUM_CID = btn.dataset.cid;
  const tr = btn.closest('tr');
  const nm = tr ? (tr.querySelector('.plink') || {{}}).textContent : '';
  document.getElementById('numparty').textContent = nm || '';
  document.getElementById('numinput').value = String(btn.dataset.num || '').slice(-10);
  document.getElementById('nummodal').classList.add('show');
  setTimeout(() => document.getElementById('numinput').focus(), 40);
}}
async function saveNumber() {{
  const raw = String(document.getElementById('numinput').value).replace(/\\D/g, '');
  if (raw.length < 10) {{ alert('Enter the 10-digit mobile number.'); return; }}
  const btn = document.getElementById('numsave'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/set-number', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: NUM_CID, number: raw}})}});
    const d = await r.json();
    if (r.ok && d.ok) {{ location.reload(); return; }}
    btn.disabled = false; btn.textContent = 'Save';
    alert(d.detail || 'Could not save that number.');
  }} catch (e) {{ btn.disabled = false; btn.textContent = 'Save'; alert('Could not save. Please try again.'); }}
}}
document.getElementById('numsave').onclick = saveNumber;
document.querySelectorAll('.phbtn').forEach(b => b.onclick = () => openNumberModal(b));

// ── Bulk-fill numbers from the owner's phone contacts (problem #4) ──
function openImportNumbers() {{
  document.getElementById('imp_text').value = '';
  document.getElementById('imp_result').innerHTML = '';
  document.getElementById('importnummodal').classList.add('show');
  setTimeout(() => document.getElementById('imp_text').focus(), 40);
}}
async function doImportNumbers() {{
  const text = document.getElementById('imp_text').value;
  if (!text.trim()) {{ alert('Paste your contacts first.'); return; }}
  const btn = document.getElementById('imp_go'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/import-numbers', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, text: text}})}});
    const d = await r.json();
    btn.disabled = false; btn.textContent = 'Fill numbers';
    if (!r.ok) {{ alert(d.detail || 'Could not import.'); return; }}
    let msg = '<b>' + (d.filled_count || 0) + ' number(s) added.</b>';
    if ((d.unmatched || []).length) msg += '<br>Not matched to a party: ' + d.unmatched.join(', ');
    if ((d.already_had || []).length) msg += '<br>Already had a number: ' + d.already_had.length;
    if ((d.bad_number || []).length) msg += '<br>Not a valid number: ' + d.bad_number.join(', ');
    document.getElementById('imp_result').innerHTML = msg;
    if (d.filled_count) setTimeout(() => location.reload(), 1600);
  }} catch (e) {{ btn.disabled = false; btn.textContent = 'Fill numbers'; alert('Could not import. Please try again.'); }}
}}
document.getElementById('imp_go').onclick = doImportNumbers;

// A banner on the party list, for trouble only: Tally not importing, or no
// customers at all. Everything routine lives on Today, which is the home
// screen - saying it twice just crowded this page.
function goPayments() {{ location.href = '/admin/payments?token=' + encodeURIComponent(TOKEN); }}
async function buildToday() {{
  const el = document.getElementById('todaycard'); if (!el) return;
  const rows = [];
  el.style.display = 'block';
  // A setup problem outranks everything: the shop is online but importing
  // nothing, so "All clear" would be a lie.
  if (SETUP_PROBLEM) {{
    el.innerHTML = '<div style="font-weight:700;margin-bottom:6px;color:#9F2F2D">ASVA cannot read your customers</div>'
      + '<div style="line-height:1.6">' + SETUP_PROBLEM + '</div>'
      + '<div style="margin-top:10px;color:#787774">Nothing has been changed in Tally. Send this message to ASVA support and we will fix the setup.</div>';
    return;
  }}
  if (!rows.length && !HAS_PARTIES) {{
    el.innerHTML = '<div style="font-weight:700;margin-bottom:6px;color:#9F2F2D">No customers imported yet</div>'
      + '<div style="line-height:1.6">ASVA has not received a single customer from Tally, so there is nothing to chase. '
      + 'Open Tally, keep your company loaded, then press <b>Get latest from Tally</b>.</div>';
    return;
  }}
  // Everything else (payments to post, paid-claims, missing numbers) is the
  // Today screen's job. Repeating it here only crowded the party list.
  el.style.display = 'none';
}}
buildToday();

// ── Create a new non-Tally party, then jump to its page to add bills ──
function openAddParty() {{
  document.getElementById('nb_name').value = '';
  document.getElementById('nb_phone').value = '';
  document.getElementById('newpartymodal').classList.add('show');
  setTimeout(() => document.getElementById('nb_name').focus(), 40);
}}
async function saveNewParty() {{
  const name = document.getElementById('nb_name').value.trim();
  const phone = document.getElementById('nb_phone').value.trim();
  if (!name) {{ alert('Enter the party name.'); return; }}
  const btn = document.getElementById('nb_save'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/party/create', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, name: name, whatsapp_number: phone}})}});
    const d = await r.json().catch(() => ({{}}));
    if (r.ok && d.client_id) {{
      location.href = '/admin/party?token=' + encodeURIComponent(TOKEN) +
        '&client_id=' + encodeURIComponent(d.client_id) + '&lang=' + encodeURIComponent(LANG);
      return;
    }}
    alert(d.detail || 'Could not create the party.');
  }} catch (e) {{ alert('Could not create. Please try again.'); }}
  btn.disabled = false; btn.textContent = 'Create';
}}
document.getElementById('nb_save').onclick = saveNewParty;

// ── "Who gets a reminder today" dry run (nothing is sent) ──────────────
async function dueToday() {{
  const box = document.getElementById('duebody');
  box.textContent = 'Loading...';
  document.getElementById('duemodal').classList.add('show');
  try {{
    const r = await fetch('/admin/due-today?token=' + encodeURIComponent(TOKEN));
    const d = await r.json();
    if (d.holiday) {{ box.innerHTML = '<b>Aaj holiday hai.</b> Koi reminder nahi jayega; agle working day chala jayega.'; return; }}
    if (!d.count) {{ box.textContent = 'Aaj kisi ko reminder nahi jayega.'; return; }}
    const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
    let h = '<b>' + d.count + ' parties</b> ko aaj reminder jayega (total ' + inr(d.total) + ').';
    if (d.capped) h += ' Pehle ' + d.cap + ' aaj, baaki agle dino.';
    h += '<br><br>' + d.parties.map(p =>
      '- ' + p.name + ': ' + inr(p.amount) + (p.kind === 'overdue' ? ' (overdue)' : '') +
      ' <button class="skipbtn" onclick="skipToday(\\'' + p.id + '\\', this)">Aaj skip</button>').join('<br>');
    if (d.count > d.parties.length) h += '<br>+ ' + (d.count - d.parties.length) + ' more';
    box.innerHTML = h;
  }} catch (e) {{ box.textContent = 'Load nahi hua.'; }}
}}

// Cancel TODAY's reminder for one party (cadence continues normally tomorrow).
async function skipToday(cid, btn) {{
  btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/skip-today', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: cid}})}});
    const d = await r.json();
    btn.textContent = (r.ok && d.skipped_points > 0) ? 'Skipped' : 'Kuch due nahi';
  }} catch (e) {{ btn.textContent = 'Fail'; btn.disabled = false; }}
}}

// ── Missed-hour catch-up banner: ASVA was off at the send hour ─────────
async function checkCatchup() {{
  try {{
    const r = await fetch('/admin/catchup-status?token=' + encodeURIComponent(TOKEN));
    const d = await r.json();
    if (!d.pending) return;
    const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
    const hrs = (d.missed_hours || []).map(h => (h < 10 ? '0' : '') + h + ':00').join(', ');
    const band = document.getElementById('catchband');
    band.innerHTML = 'ASVA band tha (' + hrs + ') - <b>' + d.count +
      ' parties</b> ke reminder ruke hain (total ' + inr(d.total) + ').' +
      '<button class="cbsend" onclick="catchup(\\'send\\')">Abhi bhejein</button>' +
      '<button class="cbskip" onclick="catchup(\\'skip\\')">Aaj skip karein</button>';
    band.style.display = 'block';
  }} catch (e) {{}}
}}
async function catchup(action) {{
  const band = document.getElementById('catchband');
  band.textContent = action === 'send'
    ? 'Bheje ja rahe hain... thodi der me status dekhein.'
    : 'Aaj ke liye skip ho gaya.';
  try {{
    await fetch('/admin/catchup', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, action: action}})}});
  }} catch (e) {{ band.textContent = 'Nahi ho paya. Page reload karke phir try karein.'; }}
  if (action === 'skip') setTimeout(() => {{ band.style.display = 'none'; }}, 4000);
}}
checkCatchup();

// ── Batch assignment: one control, two scopes ──────────────────
// The chip in the Batch column opens this list and saves on the pick - no
// dropdown to change, no Apply to remember, and nothing to tick first. Opened
// from the column header instead, it assigns every row currently shown, so the
// search box and the tabs ARE the bulk selection.
function esc(s){{ return String(s==null?'':s).replace(/[&<>]/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[m])); }}
const BATCHES = {batch_meta};
const BTINT = ['#eaf7ee','#e1f3fe','#fbf3db','#fdebec','#eee9fb'];
const BINK  = ['#0a5d28','#1f6c9f','#8a6100','#9f2f2d','#54399c'];
let BPOP_TARGET = null;      // a chip element, or null = every row shown

function shownChips() {{
  return [...document.querySelectorAll('#ptable tr[data-name]')]
    .filter(r => r.style.display !== 'none')
    .map(r => r.querySelector('.bchip')).filter(Boolean);
}}
function openBatchPop(chip, anchor) {{
  BPOP_TARGET = chip;
  const pop = document.getElementById('bpop');
  const cur = chip ? parseInt(chip.dataset.b) : -1;
  const n = chip ? 1 : shownChips().length;
  document.getElementById('bpophd').textContent = chip
    ? 'Batch for this party'
    : 'Put all ' + n + ' parties shown into';
  document.getElementById('bpoplist').innerHTML = BATCHES.map(b =>
    '<button class="bpopitem' + (b.i === cur ? ' cur' : '') + '" data-i="' + b.i + '">' +
    '<b>' + (b.i + 1) + '.</b> ' + esc(b.name) +
    '<span class="bl">' + esc(b.lbl) + '</span></button>').join('');
  [...pop.querySelectorAll('.bpopitem')].forEach(it =>
    it.onclick = () => pickBatch(parseInt(it.dataset.i)));
  // Place it under whatever was clicked, pulled back inside the window.
  const r = anchor.getBoundingClientRect();
  pop.classList.add('show');
  const w = pop.offsetWidth;
  pop.style.left = Math.max(8, Math.min(r.left + window.scrollX, window.innerWidth - w - 10)) + 'px';
  pop.style.top = (r.bottom + window.scrollY + 6) + 'px';
}}
function closeBatchPop() {{
  document.getElementById('bpop').classList.remove('show');
  BPOP_TARGET = null;
}}
document.addEventListener('click', ev => {{
  if (ev.target.closest('#bpop') || ev.target.closest('.bchip') || ev.target.closest('.bhead')) return;
  closeBatchPop();
}});
document.addEventListener('keydown', ev => {{ if (ev.key === 'Escape') closeBatchPop(); }});

function paintChip(chip, i) {{
  const b = BATCHES[i] || {{i: i, name: 'Batch ' + (i + 1)}};
  chip.dataset.b = i;
  chip.innerHTML = '<b>' + (i + 1) + '</b> ' + esc(b.name);
  chip.style.background = BTINT[i % 5];
  chip.style.color = BINK[i % 5];
}}
async function pickBatch(i) {{
  const chips = BPOP_TARGET ? [BPOP_TARGET] : shownChips();
  const bulk = !BPOP_TARGET;
  closeBatchPop();
  if (!chips.length) return;
  if (bulk && !confirm('Put all ' + chips.length + ' parties shown into batch ' +
      (i + 1) + ' (' + (BATCHES[i] || {{}}).name + ')?')) return;
  const ids = chips.map(ch => ch.dataset.cid);
  chips.forEach(ch => ch.classList.add('saving'));
  const msg = document.getElementById('msg');
  msg.textContent = 'Saving...';
  try {{
    const r = await fetch('/admin/assign-batch', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_ids: ids, batch: i}})}});
    if (!r.ok) throw new Error('http');
    chips.forEach(ch => paintChip(ch, i));
    msg.textContent = chips.length === 1
      ? 'Saved'
      : chips.length + ' parties → batch ' + (i + 1);
  }} catch (e) {{
    msg.textContent = 'Could not save. Check your connection and try again.';
  }}
  chips.forEach(ch => ch.classList.remove('saving'));
  setTimeout(() => {{ if (msg.textContent.indexOf('Could not') < 0) msg.textContent = ''; }}, 3000);
}}
document.querySelectorAll('.bchip').forEach(ch =>
  ch.onclick = (ev) => {{ ev.stopPropagation(); openBatchPop(ch, ch); }});


// ── Per-party credit days + read-only reminder schedule ──────────────
const BASE_CADENCE = {base_cadence_json};   // reminder timing is ASVA's logic
let TERM_CID = null;
function schedText(cd) {{
  cd = cd || 30;
  const days = BASE_CADENCE.map(d => Math.max(1, Math.min(cd, Math.round(d * cd / 30))));
  const uniq = [...new Set(days)];
  // Must stay the same arithmetic as party_repeat_days() in reminder_sweep.py.
  // These two formulas disagreed for months: this screen scaled on the party's
  // credit days and said 14, while the engine scaled on the BILL's stored term,
  // which Tally left at 0, so it actually sent every 7.
  const ov = Math.max(7, Math.round(7 * cd / 30));
  return 'Reminders go out on these days after the bill:\\n  day ' + uniq.join(', ') + '\\n' +
         'Then one overdue reminder every ' + ov + ' days, for this party as a\\n' +
         'whole however many bills are open, up to 300 days past due.\\n' +
         'After that ASVA stops and asks you to escalate.\\n\\n' +
         '(ASVA sets the timing. You only set the credit days.)';
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
    else {{ btn.disabled = false; btn.textContent = 'Save'; alert('Save failed'); }}
  }} catch (e) {{ btn.disabled = false; btn.textContent = 'Save'; alert('Save failed'); }}
}}
</script></body></html>"""
    return HTMLResponse(_ui_translate(html, lang))


@router.get("/admin/reminders", response_class=HTMLResponse)
def admin_reminders(token: str = Query(...), lang: str = Query("english")):
    """Reminder settings on their own page (its own desktop tab): language, send
    time, weekly off, holidays, custom line, early-pay discount, and preview.
    Timing itself is ASVA's logic (per-party credit days), not editable here."""
    biz = _biz_by_token(token)
    # Customer sends only drain during shop hours, so the batch time picker
    # must not offer an hour the sender will ignore (a batch set to 23:00
    # looked accepted, then sat in the outbox until 10 AM next day).
    send_from = settings.send_window_start_hour if settings.enforce_send_window else 0
    send_to = settings.send_window_end_hour if settings.enforce_send_window else 24

    festivals = sorted(str(d) for d in (biz.get("blackout_dates") or []))
    festivals_json = json.dumps(festivals)

    # The weekly off day was being wiped on every holiday save (the JS posted a
    # hardcoded null), and the sweep never read it anyway, so no shop has ever
    # had one. Render the stored value so saving holidays stops destroying it.
    from app.models import resolve_default_credit_days
    _woff = biz.get("weekly_off_day")
    woff_json = json.dumps(_woff if _woff is not None else None)
    send_on_holidays_js = "true" if biz.get("send_on_holidays") else "false"
    default_credit_days = resolve_default_credit_days(biz)

    upi_vpa = biz.get("upi_vpa") or ""
    upi_vpa_2 = biz.get("upi_vpa_2") or ""
    upi_vpa_3 = biz.get("upi_vpa_3") or ""
    _bank_no = (biz.get("bank_account_no") or "").strip()
    bank_set_js = "true" if _bank_no else "false"
    _bank_nm = (biz.get("bank_name") or "").strip()
    bank_label = ("Bank: " + _bank_nm) if _bank_nm else ("Bank transfer (A/c " + _bank_no[-4:] + ")" if _bank_no else "Bank transfer")

    from app.services.batches import get_batches, BANK_SENTINEL, CASH_SENTINEL
    batches_json = json.dumps(get_batches(biz))
    bank_sentinel_js = json.dumps(BANK_SENTINEL)
    cash_sentinel_js = json.dumps(CASH_SENTINEL)
    # Batch editor JS kept as a plain string (single braces) and interpolated, so
    # it needs no f-string brace-doubling.
    batch_js = r"""
var LANGS=[['english','English'],['hinglish','Hinglish']];
function besc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}
function bopts(list,val){return list.map(function(o){return '<option value="'+o[0]+'"'+(o[0]===val?' selected':'')+'>'+o[1]+'</option>';}).join('');}
// Only offer hours the sender will actually honour. Customer sends drain
// between SEND_FROM and SEND_TO (shop hours); a batch set to 23:00 used to look
// accepted and then sit in the outbox until the window reopened next morning,
// so the owner picked 11 PM and messages left at 10 AM. Out-of-window hours are
// still listed if already saved, marked so the reason is visible.
function hourOpts(val){var v=(val==null?11:Number(val));var s='';
  for(var h=0;h<24;h++){
    var inWin=(h>=SEND_FROM&&h<SEND_TO);
    if(!inWin&&h!==v) continue;
    var hh=(h<10?'0':'')+h;
    s+='<option value="'+h+'"'+(h===v?' selected':'')+'>'+hh+':00'
      +(inWin?'':' (outside shop hours)')+'</option>';
  }
  return s;}
function upiOpts(val) {
  var opts = [['', 'Shop Default (UPI)']];
  if (typeof UPI_1 !== 'undefined' && UPI_1) opts.push([UPI_1, 'UPI 1: ' + UPI_1]);
  if (typeof UPI_2 !== 'undefined' && UPI_2) opts.push([UPI_2, 'UPI 2: ' + UPI_2]);
  if (typeof UPI_3 !== 'undefined' && UPI_3) opts.push([UPI_3, 'UPI 3: ' + UPI_3]);
  if (typeof BANK_SET !== 'undefined' && BANK_SET) opts.push([BANK_VAL, BANK_LABEL]);
  // Cash is always selectable - it needs no account. A cash batch's reminder
  // carries no UPI link, no QR and no bank details (no digital trail).
  if (typeof CASH_VAL !== 'undefined') opts.push([CASH_VAL, 'Cash (no online payment)']);
  if (val && !opts.some(function(o){ return o[0] === val; })) {
    // A saved bank/custom value whose account is not (yet) configured.
    opts.push([val, val === BANK_VAL ? 'Bank transfer' : (val === CASH_VAL ? 'Cash (no online payment)' : ('Custom: ' + val))]);
  }
  return opts.map(function(o){
    return '<option value="'+o[0]+'"'+(o[0]===val?' selected':'')+'>'+besc(o[1])+'</option>';
  }).join('');
}
function renderBatches(){
  var listEl = document.getElementById('batchlist');
  var headerHtml = '<div class="batch-headers">'
    +'<div class="hcol-num">#</div>'
    +'<div class="hcol-name">Batch Name</div>'
    +'<div class="hcol-lang">Language</div>'
    +'<div class="hcol-upi">Payment Account</div>'
    +'<div class="hcol-time">Send Time</div>'
    +'<div class="hcol-disc">Discount</div>'
    +'<div class="hcol-acts"></div>'
    +'</div>';

  listEl.innerHTML = headerHtml + BATCHES.map(function(b,i){
    return '<div class="brow"><span class="bnum">'+(i+1)+'</span>'
      +'<input class="bname" value="'+besc(b.name)+'" placeholder="Name">'
      +'<select class="blang" title="Language">'+bopts(LANGS,b.lang)+'</select>'
      +'<select class="bupi" title="Payment Account">'+upiOpts(b.upi||'')+'</select>'
      +'<select class="btime" title="Send time">'+hourOpts(b.hour)+'</select>'
      +'<div class="bdisc-wrap"><input class="bdisc" type="number" min="0" max="50" step="0.5" value="'+(b.disc||0)+'" title="Optional early-pay discount %"><span class="pct">%</span></div>'
      +'<div class="bacts">'
        +'<button type="button" class="btn2 bprev" onclick="previewBatch('+i+')">Preview</button>'
        +(BATCHES.length>1?'<button type="button" class="brm" onclick="removeBatch('+i+')" title="Remove">x</button>':'')
      +'</div>'
      +'</div>';
  }).join('');
}
function collectBatches(){
  return Array.prototype.slice.call(document.querySelectorAll('#batchlist .brow')).map(function(r){
    return {name:r.querySelector('.bname').value,
      lang:r.querySelector('.blang').value,
      upi:r.querySelector('.bupi').value,
      hour:parseInt(r.querySelector('.btime').value,10),
      disc:parseFloat(r.querySelector('.bdisc').value)||0};
  });
}
function addBatch(){ if(BATCHES.length>=5){return;} BATCHES=collectBatches(); var dh=(BATCHES[0]&&BATCHES[0].hour!=null)?BATCHES[0].hour:11; BATCHES.push({name:'Batch '+(BATCHES.length+1),lang:'english',upi:'',hour:dh,disc:0}); renderBatches(); }
removeBatch = function(i){ BATCHES=collectBatches(); BATCHES.splice(i,1); renderBatches(); }
function saveBatches(){
  var msg=document.getElementById('batchmsg'); msg.textContent='Saving...';
  fetch('/admin/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TOKEN,batches:collectBatches()})})
    .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
    .then(function(x){ if(x.ok){ BATCHES=x.d.batches; renderBatches(); msg.textContent='Saved'; } else { msg.textContent='Save failed'; } })
    .catch(function(){ msg.textContent='Save failed'; });
}
function previewBatch(i){
  var b=collectBatches()[i]; if(!b){return;}
  var box=document.getElementById('prevtext'); box.textContent='Loading...';
  document.getElementById('prevmodal').classList.add('show');
  var p=new URLSearchParams({token:TOKEN,language:b.lang,discount_pct:String(b.disc||0),upi:b.upi||'',batch:String(i)});
  // no-store: two batches with the same language and discount make the same
  // URL, and a cached reply is the other way a preview looks stuck.
  fetch('/admin/preview?'+p.toString(),{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){box.textContent=d.message||'Preview not available.';}).catch(function(){box.textContent='Preview failed.';});
}
renderBatches();
"""

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{biz['business_name']} - Reminders</title>{_FAVICON}
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:0;background:#F7F6F3;color:#2F3437}}
 .wrap{{max-width:980px;margin:0 auto;padding:20px 16px}}
 h2{{margin:0 0 4px}} .sub{{color:#787774;margin-bottom:16px;font-size:.95em}}
 .card{{margin:14px 0;padding:20px;border:1px solid #EAEAEA;border-radius:12px;background:#fff}}
 .card h3{{margin:0 0 14px;font-size:1.05rem}}
 .row{{display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap}}
 .row>label{{min-width:150px;font-weight:600}}
 button{{font:inherit;cursor:pointer;transition:background 150ms ease,transform 120ms ease-out}}
 button:active{{transform:scale(.97)}}
 input,select{{padding:8px;font-size:1em;border:1px solid #ddd;border-radius:6px}}
 #setmsg{{color:#0a7d33;font-weight:600;margin-left:8px}}
 #savehol{{background:#0a7d33;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:1em}}
 .btn2{{background:#fff;border:1px solid #EAEAEA;border-radius:8px;padding:9px 16px;font-size:.95em}}
 .btn2:hover{{background:#f4f4f1}}
 .calwrap{{max-width:340px}}
 .calhead{{display:flex;align-items:center;justify-content:space-between;margin:4px 0 14px}}
 .calhead button{{background:#fff;border:1px solid #ddd;border-radius:6px;padding:4px 12px;font-size:1em;color:#555;cursor:pointer}}
 .calhead span{{font-size:1.5rem;font-weight:700;color:#1f6c9f;flex:1;text-align:center}}
 .calgrid{{display:grid;grid-template-columns:repeat(7,1fr)}}
 .dow{{text-align:center;font-size:.82em;color:#787774;font-weight:600;padding-bottom:8px}}
 .calrule{{grid-column:1/-1;border-bottom:1px solid #e2e2e0;margin-bottom:8px}}
 .day{{text-align:center;padding:9px 0;border-radius:50%;cursor:pointer;font-size:.95em}}
 .day:hover{{background:#eef3ef}}
 .day.today{{box-shadow:inset 0 0 0 2px #0a7d33;color:#0a7d33;font-weight:700}}
 .day.hol{{background:#e23b2d;color:#fff;font-weight:700}}
 .day.hol:hover{{background:#c92f22}}
 .day.past{{color:#ccc;cursor:default}}
 .day.past:hover{{background:transparent}}
 .day.blank{{cursor:default}}
 .day.blank:hover{{background:transparent}}
 .holist{{margin-top:14px;font-size:.9em;color:#787774;max-width:340px}}
 .holchip{{display:inline-block;background:#fdebec;color:#9f2f2d;border-radius:6px;padding:3px 9px;margin:4px 5px 0 0}}
 .hint{{color:#787774;font-size:.85em;margin-top:6px}}
 .modal{{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:9}}
 .modal.show{{display:flex}}
 .modalbox{{background:#fff;max-width:420px;width:90%;border-radius:14px;padding:18px 20px}}
 .msgprev{{white-space:pre-wrap;background:#f6f6f4;border:1px solid #eee;border-radius:8px;padding:12px;font-size:.95em;line-height:1.5;max-height:50vh;overflow:auto}}
  /* On a narrow phone screen the fixed-width columns scroll sideways inside
     the list instead of squashing or spilling off the page. */
  #batchlist {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  .batch-headers, .brow {{ min-width: 640px; }}
  .batch-headers {{
    display: flex;
    gap: 10px;
    padding: 0 12px 6px;
    font-weight: 600;
    font-size: 0.85em;
    color: #787774;
    border-bottom: 1px solid #EAEAEA;
    margin-bottom: 8px;
  }}
  .hcol-num {{ width: 24px; flex-shrink: 0; }}
  .hcol-name {{ flex: 1; min-width: 120px; }}
  .hcol-lang {{ width: 100px; }}
  .hcol-upi {{ width: 160px; }}
  .hcol-time {{ width: 100px; }}
  .hcol-disc {{ width: 90px; }}
  .hcol-acts {{ width: 130px; text-align: right; }}

  .brow {{
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px 12px;
    border: 1px solid #EAEAEA;
    border-radius: 8px;
    background: #fff;
    margin-bottom: 8px;
  }}
  .bnum {{
    width: 24px;
    height: 24px;
    line-height: 24px;
    text-align: center;
    border-radius: 50%;
    background: #294d38;
    color: #fff;
    font-weight: 700;
    font-size: .8rem;
    flex-shrink: 0;
  }}
  .bname {{ flex: 1; min-width: 120px; }}
  .blang {{ width: 100px; }}
  .bupi {{ width: 160px; }}
  .btime {{ width: 100px; }}
  .bdisc-wrap {{ display: inline-flex; align-items: center; gap: 4px; width: 90px; }}
  .bdisc {{ width: 60px; }}
  .pct {{ color: #787774; font-size: .85em; }}
  .bacts {{ width: 130px; display: inline-flex; gap: 6px; justify-content: flex-end; }}
  .brm {{ border: 1px solid #EAEAEA; background: #fff; border-radius: 6px; padding: 7px 10px; color: #c0392b; font-weight: 700; cursor: pointer; }}
  .brm:hover {{ background: #fdebec; }}
  .bprev {{ padding: 7px 12px; }}

  #savebatch{{background:#0a7d33;color:#fff;border:0;border-radius:6px;padding:9px 18px;font-size:1em}}
  #batchmsg{{color:#0a7d33;font-weight:600}}
  .guide{{background:#f4f9fd;border-left:3px solid #bfe2f7;border-radius:0 8px 8px 0;padding:9px 14px;margin:0 0 14px;font-size:.88em;line-height:1.6;color:#4a6b82}}
  details.guide summary{{cursor:pointer;font-weight:600;outline:none}}
  details.guide[open] summary{{margin-bottom:6px}}
{_NAV_CSS}
 </style></head><body>
 <div class="wrap">
 {_topnav(token, lang, "reminders")}
 <h2>Reminders</h2>
 <div class="sub">Batch = language + UPI + time ka ek group. Dashboard me har party ko batch dein; message apne aap jaate hain.</div>

 <div class="card">
  <h3>Reminder batches</h3>
  <details class="guide"><summary>Reminder kaise chalta hai (sab automatic):</summary>
1. Naya bill = credit period ke andar vinamra yaad (30 din me 5 baar).<br>
2. Due date ke baad = har ~7 din me overdue message, jab tak payment na aaye (max 200 din). Lambi credit walon ko utna hi lamba gap.<br>
3. Phir ASVA aapko bolega: ab khud call karein.<br>
4. Jis din party SELECT karo, ginti usi din se - overdue party ko usi din overdue message jata hai.<br>
Ek party = EK message: saare bills jodkar, total + QR ke saath.</details>
  <div id="batchlist"></div>
  <div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button type="button" class="btn2" onclick="addBatch()">+ Batch add karein</button>
    <button id="savebatch" onclick="saveBatches()">Save batches</button>
    <span id="batchmsg"></span>
  </div>
 </div>

 <div class="card">
  <h3>Closed days</h3>
  <div style="margin-bottom:10px">
   <label for="woff">Weekly off</label>
   <select id="woff">
    <option value="">Open every day</option>
    <option value="0">Monday</option><option value="1">Tuesday</option>
    <option value="2">Wednesday</option><option value="3">Thursday</option>
    <option value="4">Friday</option><option value="5">Saturday</option>
    <option value="6">Sunday</option>
   </select>
  </div>
  <div class="calwrap">
   <div class="calhead"><button onclick="calMove(-1)">&#9664;</button><span id="calLabel"></span><button onclick="calMove(1)">&#9654;</button></div>
   <div id="calGrid" class="calgrid"></div>
  </div>
  <div class="hint">Tap a future date to mark it a holiday (red). Reminders skip that day and go out on the next working day instead.</div>
  <div id="holist" class="holist"></div>
  <label style="display:block;margin-top:12px">
   <input type="checkbox" id="sendhol"> Send reminders on my off day and holidays too
  </label>
  <div class="hint">Leave this unticked and a reminder due on a closed day waits for the next working day. Tick it if you trade through Sundays and festivals.</div>
  <div style="margin-top:14px">
   <label for="defcd">Credit days when Tally does not say</label>
   <select id="defcd">
    <option value="30">30 days</option>
    <option value="45">45 days</option>
    <option value="60">60 days</option>
   </select>
   <div class="hint">Tally only stores a credit period on some ledgers. This fills the blanks. A party whose days you set yourself, or that Tally does report, is never changed.</div>
  </div>
  <div style="margin-top:10px"><button id="savehol" onclick="saveHolidays()">Save</button><span id="setmsg"></span></div>
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
 const SEND_FROM = {send_from}, SEND_TO = {send_to};   // hours the sender actually honours

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

 const UPI_1 = {upi_vpa!r};
 const UPI_2 = {upi_vpa_2!r};
 const UPI_3 = {upi_vpa_3!r};
 const BANK_SET = {bank_set_js};
 const BANK_LABEL = {bank_label!r};
 const BANK_VAL = {bank_sentinel_js};
 const CASH_VAL = {cash_sentinel_js};
 let FEST = {festivals_json};
 let BATCHES = {batches_json};
 let calY, calM;
{batch_js}
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
  let h = ['S','M','T','W','T','F','S'].map(d => `<div class="dow">${{d}}</div>`).join('');
  h += '<div class="calrule"></div>';
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

// Fill the three controls from what is stored, so a save never silently
// discards a setting the owner is not currently looking at. This posted a
// hardcoded weekly_off_day:null, which wiped the off day on every holiday save.
(function initClosedDays() {{
  const woff = {woff_json};
  document.getElementById('woff').value = (woff === null ? '' : String(woff));
  document.getElementById('sendhol').checked = {send_on_holidays_js};
  document.getElementById('defcd').value = String({default_credit_days});
}})();

async function saveHolidays() {{
  document.getElementById('setmsg').textContent = 'Saving...';
  const raw = document.getElementById('woff').value;
  const r = await fetch('/admin/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      token: TOKEN,
      weekly_off_day: raw === '' ? null : parseInt(raw, 10),
      blackout_dates: FEST,
      send_on_holidays: document.getElementById('sendhol').checked,
      default_credit_days: parseInt(document.getElementById('defcd').value, 10)
    }})}});
  document.getElementById('setmsg').textContent = r.ok ? 'Saved' : 'Save failed';
}}
</script></body></html>"""
    return HTMLResponse(_ui_translate(html, lang))


class SavePayload(BaseModel):
    token: str
    enabled_ids: list[str]


@router.post("/admin/save")
def admin_save(payload: SavePayload):
    biz = _biz_by_token(payload.token)
    db = require_db()

    all_ids = []
    was_enabled: set = set()
    start = 0
    while True:
        resp = (db.table("clients").select("id, reminders_enabled")
                .eq("business_id", biz["id"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        all_ids.extend(c["id"] for c in batch)
        was_enabled.update(c["id"] for c in batch if c.get("reminders_enabled"))
        if len(batch) < 1000:
            break
        start += 1000

    enabled = set(payload.enabled_ids) & set(all_ids)
    disabled = [i for i in all_ids if i not in enabled]

    # NEWLY selected parties get today's anchor ("counting starts from the
    # day you select"); parties that were already ON keep their clock.
    newly = sorted(enabled - was_enabled)
    today = _today().isoformat()
    for chunk in _chunked(newly):
        db.table("clients").update(
            {"reminders_enabled": True, "reminder_anchor": today}).in_("id", chunk).execute()
    for chunk in _chunked(sorted(enabled & was_enabled)):
        db.table("clients").update({"reminders_enabled": True}).in_("id", chunk).execute()
    for chunk in _chunked(disabled):
        db.table("clients").update({"reminders_enabled": False}).in_("id", chunk).execute()

    return {"enabled": len(enabled), "disabled": len(disabled), "newly_selected": len(newly)}


class ReloadPayload(BaseModel):
    token: str


@router.post("/admin/reload")
def admin_reload(payload: ReloadPayload):
    """Override switch: force the Tally agent to refresh outstanding NOW instead
    of waiting for its 5-min auto cycle. Rate-limited to once per 10 minutes so
    a big shop's single-threaded Tally isn't hammered. The agent polls
    /tally/pending-refresh, runs the refresh, and clears the flag."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        r = (db.table("businesses").select("refresh_requested_at")
             .eq("id", biz["id"]).limit(1).execute())
        prev = _parse_ts(r.data[0].get("refresh_requested_at")) if r.data else None
    except Exception:
        raise HTTPException(status_code=503,
                            detail="Reload feature needs migration 015. Apply it, then retry.")
    if prev:
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=_dt.timezone.utc)
        elapsed = (now - prev).total_seconds()
        if elapsed < RELOAD_COOLDOWN_MIN * 60:
            wait = int((RELOAD_COOLDOWN_MIN * 60 - elapsed) // 60) + 1
            return {"ok": False, "cooldown": True, "wait_min": wait,
                    "detail": f"You can reload again in {wait} min."}

    db.table("businesses").update({"refresh_requested_at": now.isoformat()}).eq("id", biz["id"]).execute()
    return {"ok": True, "requested_at": now.isoformat()}


@router.post("/admin/reconcile")
def admin_reconcile(payload: ReloadPayload):
    """Owner pressed "Clean up removed parties" (with a confirmation). Flags a
    reconcile: the agent's next outstanding push archives any Tally-sourced party
    no longer in Tally (soft, reversible - see tally._run_reconcile). Also sets the
    normal refresh flag so it runs on the very next tick, not the 5-min cycle."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        db.table("businesses").update({
            "refresh_requested_at": now, "reconcile_requested_at": now,
        }).eq("id", biz["id"]).execute()
    except Exception:
        raise HTTPException(status_code=503,
                            detail="Clean-up needs migration 049. Apply it, then retry.")
    return {"ok": True, "requested_at": now}


@router.get("/admin/sync-status")
def admin_sync_status(token: str = Query(...), lang: str = Query("english")):
    """Lightweight poll for the dashboard: last Tally sync time + whether a
    manual reload is still pending. The page polls this after Reload and
    re-renders once the sync completes (last_synced advances / flag clears)."""
    biz = _biz_by_token(token)
    db = require_db()
    last = _last_synced_at(db, biz["id"])
    pending = False
    try:
        r = (db.table("businesses").select("refresh_requested_at")
             .eq("id", biz["id"]).limit(1).execute())
        pending = bool(r.data and r.data[0].get("refresh_requested_at"))
    except Exception:
        pending = False
    tally_label, tally_color = _tally_status(last)
    # These strings are authored in Hinglish like the pages, but this is JSON:
    # the page-level translate pass never sees it, so localize here or the
    # desktop top bar reads "Tally se contact nahi" in English mode.
    tally_label = i18n.translate_html(tally_label, lang)
    # Onboarding-tour signals (also power the setup checklist): whether UPI is set,
    # the owner's own number + the ASVA bot number to message, and the timestamp of
    # the last inbound message (so the tour can confirm the owner actually messaged
    # the bot). All cheap: one small indexed query + fields already on `biz`.
    from app.config import settings as _cfg
    owner_raw = "".join(ch for ch in str(biz.get("whatsapp_number") or "") if ch.isdigit())
    owner_disp = owner_raw[-10:] if len(owner_raw) >= 10 else owner_raw
    last_inbound = None
    try:
        ib = (db.table("messages").select("created_at")
              .eq("business_id", biz["id"]).eq("template_name", "inbound")
              .order("created_at", desc=True).limit(1).execute())
        if ib.data:
            last_inbound = ib.data[0].get("created_at")
    except Exception:
        last_inbound = None
    return {
        "last_synced_at": last.isoformat() if last else None,
        "last_synced_label": i18n.translate_html(_fmt_ago(last), lang),
        "pending_refresh": pending,
        "tally_label": tally_label,
        "tally_color": tally_color,
        "upi": bool(biz.get("upi_vpa")),
        "owner_number": owner_disp,
        "bot_number": _cfg.asva_bot_number,
        "last_inbound_at": last_inbound,
    }


@router.get("/admin/due-today")
def admin_due_today(token: str = Query(...)):
    """Dry run: exactly which parties would get a reminder in today's sweep, using
    the SAME cadence + dedup logic the sweep uses. Nothing is sent. So the owner
    is never surprised by who gets messaged."""
    from app.jobs.reminder_sweep import (cadence_points, latest_reached_point,
                                          DEFAULT_CADENCE, STYLE_CADENCE)
    from app.services.batches import resolve_batch
    from app.config import settings as _s
    biz = _biz_by_token(token)
    db = require_db()
    today = _today()
    blackout = {str(d) for d in (biz.get("blackout_dates") or [])}
    holiday = today.isoformat() in blackout

    # Already-sent (bill, reminder_day) pairs for this business, fetched once.
    sent_pairs: set = set()
    start = 0
    while True:
        r = (db.table("messages").select("bill_id, reminder_day")
             .eq("business_id", biz["id"]).eq("type", "reminder")
             .neq("delivery_status", "failed")   # failed = retried, like the sweep
             .range(start, start + 999).execute()).data or []
        for m in r:
            if m.get("bill_id") is not None and m.get("reminder_day") is not None:
                sent_pairs.add((m["bill_id"], m["reminder_day"]))
        if len(r) < 1000:
            break
        start += 1000

    bills = _fetch_paged(db, "bills",
                         "id, invoice_number, outstanding, due_date, invoice_date, client_id, "
                         "clients(id, name, whatsapp_number, reminders_enabled, credit_days, "
                         "reminder_batch, reminder_anchor, created_at)",
                         biz["id"], status_in=["pending", "partial", "overdue"])

    from collections import defaultdict
    agg: dict = defaultdict(lambda: {"name": "", "amount": Decimal(0), "kind": "nudge"})
    for b in bills:
        client = b.get("clients") or {}
        if not client.get("reminders_enabled", True):
            continue
        try:
            inv = _dt.date.fromisoformat(str(b["invoice_date"]))
        except (TypeError, ValueError):
            continue
        due = b.get("due_date")
        try:
            due_d = _dt.date.fromisoformat(str(due)) if due else inv
        except (TypeError, ValueError):
            due_d = inv
        _a = _client_anchor(client)
        pts = cadence_points(
            cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
            repeat_days=biz.get("overdue_repeat_days") or 7,
            max_repeats=biz.get("overdue_max_repeats") or 3,
            credit_days=client.get("credit_days") or 30,
            due_offset=(due_d - inv).days,
            overdue_from=((_a - inv).days if (_a and _a > due_d) else None))
        applicable = latest_reached_point(pts, (today - inv).days)
        if applicable is None:
            continue
        day, kind = applicable
        if (b["id"], day) in sent_pairs:
            continue
        if kind != "escalate" and not client.get("whatsapp_number"):
            continue
        cid = b["client_id"]
        e = agg[cid]
        e["id"] = cid
        e["name"] = client.get("name", "?")
        e["amount"] += Decimal(str(b.get("outstanding") or 0))
        if kind in ("overdue", "escalate"):
            e["kind"] = "overdue"
    parties = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)
    cap = _s.daily_reminder_cap
    return {
        "holiday": holiday,
        "count": len(parties),
        "total": float(sum(p["amount"] for p in parties)),
        "cap": cap,
        "capped": cap > 0 and len(parties) > cap,
        "parties": [{"id": p["id"], "name": p["name"], "amount": float(p["amount"]),
                     "kind": p["kind"]}
                    for p in parties[:60]],
    }


# ── Missed-hour catch-up: status + owner decision (Send now / Skip today) ──
def _missed_batch_hours(db, biz: dict, now_ist) -> list[int]:
    """Batch hours that already passed today WITHOUT a sweep run at that hour
    (= ASVA was off then). Empty when migration 022 is missing."""
    from app.services.batches import get_batches, batch_hour
    try:
        rows = (db.table("sweep_runs").select("run_hour")
                .eq("run_date", now_ist.date().isoformat()).execute()).data or []
    except Exception:
        return []
    hours_run = {int(r["run_hour"]) for r in rows}
    bhs = {batch_hour(biz, b) for b in get_batches(biz)}
    return sorted(h for h in bhs if h < now_ist.hour and h not in hours_run)


@router.get("/admin/catchup-status")
def admin_catchup_status(token: str = Query(...)):
    """Is a missed-hour catch-up waiting for the owner's decision? Powers the
    dashboard banner: 'ASVA was off at HH:00 - N reminders waiting. Send / Skip.'"""
    from app.jobs.reminder_sweep import IST
    biz = _biz_by_token(token)
    db = require_db()
    now_ist = _dt.datetime.now(IST)
    today = now_ist.date().isoformat()

    decided = None
    if str(biz.get("catchup_date") or "")[:10] == today:
        decided = (biz.get("catchup_action") or "").strip().lower() or None

    missed = _missed_batch_hours(db, biz, now_ist)
    if not missed or decided:
        return {"pending": False, "decision": decided, "missed_hours": missed}

    due = admin_due_today(token)
    if due.get("holiday") or not due.get("count"):
        return {"pending": False, "decision": decided, "missed_hours": missed}
    return {"pending": True, "decision": None, "missed_hours": missed,
            "count": due["count"], "total": due["total"]}


class CatchupPayload(BaseModel):
    token: str
    action: str  # 'send' | 'skip'


@router.post("/admin/catchup")
def admin_catchup(payload: CatchupPayload, background_tasks: BackgroundTasks):
    """Owner's decision on held catch-up reminders. 'send' releases them (the
    sweep runs immediately in the background); 'skip' drops them for today -
    the cadence continues normally tomorrow, nothing stacks."""
    from app.jobs import reminder_sweep
    action = (payload.action or "").strip().lower()
    if action not in ("send", "skip"):
        raise HTTPException(status_code=400, detail="action must be 'send' or 'skip'")
    biz = _biz_by_token(payload.token)
    db = require_db()
    db.table("businesses").update({
        "catchup_date": _dt.datetime.now(reminder_sweep.IST).date().isoformat(),
        "catchup_action": action,
    }).eq("id", biz["id"]).execute()
    if action == "send":
        background_tasks.add_task(reminder_sweep.run)
    return {"ok": True, "action": action}


class SkipTodayPayload(BaseModel):
    token: str
    client_id: str


@router.post("/admin/skip-today")
def admin_skip_today(payload: SkipTodayPayload):
    """Cancel TODAY's reminder for one party: mark today's reached cadence
    points as 'skipped' so the sweep won't send them. Tomorrow the cadence
    continues normally (nothing stacks - latest point only)."""
    from app.jobs.reminder_sweep import (cadence_points, latest_reached_point,
                                         DEFAULT_CADENCE)
    biz = _biz_by_token(payload.token)
    db = require_db()
    today = _today()

    cr = (db.table("clients")
          .select("id, name, credit_days, reminder_anchor, created_at")
          .eq("id", payload.client_id).eq("business_id", biz["id"])
          .limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    client = cr.data[0]
    anchor = _client_anchor(client)

    bills = (db.table("bills")
             .select("id, invoice_date, due_date")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []

    marked = 0
    for b in bills:
        try:
            inv = _dt.date.fromisoformat(str(b["invoice_date"]))
        except (TypeError, ValueError):
            continue
        due = b.get("due_date")
        try:
            due_d = _dt.date.fromisoformat(str(due)) if due else inv
        except (TypeError, ValueError):
            due_d = inv
        pts = cadence_points(
            cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
            repeat_days=biz.get("overdue_repeat_days") or 7,
            max_repeats=biz.get("overdue_max_repeats") or 3,
            credit_days=client.get("credit_days") or 30,
            due_offset=(due_d - inv).days,
            overdue_from=((anchor - inv).days if (anchor and anchor > due_d) else None))
        applicable = latest_reached_point(pts, (today - inv).days)
        if applicable is None:
            continue
        day, _kind = applicable
        dup = (db.table("messages").select("id", count="exact")
               .eq("bill_id", b["id"]).eq("reminder_day", day)
               .eq("type", "reminder").neq("delivery_status", "failed")
               .limit(1).execute())
        if dup.data:
            continue
        db.table("messages").insert({
            "business_id": biz["id"],
            "client_id": payload.client_id,
            "bill_id": b["id"],
            "type": "reminder",
            "reminder_day": day,
            "template_name": "skipped_by_owner",
            "language": "hi",
            "delivery_status": "skipped",
            "cost": 0,
        }).execute()
        marked += 1
    return {"ok": True, "skipped_points": marked}


class BatchesPayload(BaseModel):
    token: str
    batches: list[dict]


@router.post("/admin/batches")
def admin_save_batches(payload: BatchesPayload):
    """Save the business's reminder batches (up to 5). Each batch carries its own
    severity (style), language, early-pay discount and custom line."""
    from app.services.batches import normalize_batches
    biz = _biz_by_token(payload.token)
    db = require_db()
    clean = normalize_batches(payload.batches)
    db.table("businesses").update({"reminder_batches": clean}).eq("id", biz["id"]).execute()
    return {"ok": True, "batches": clean}


class AssignBatchPayload(BaseModel):
    token: str
    client_ids: list[str]
    batch: int


@router.post("/admin/assign-batch")
def admin_assign_batch(payload: AssignBatchPayload):
    """Assign one or more parties to a reminder batch (0-4)."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    b = max(0, min(4, int(payload.batch)))
    ids = [i for i in (payload.client_ids or []) if i]
    if not ids:
        return {"ok": True, "assigned": 0}
    # Scope to this business, then update in chunks.
    valid = set()
    for chunk in _chunked(ids, 100):
        r = (db.table("clients").select("id").eq("business_id", biz["id"])
             .in_("id", chunk).execute())
        valid.update(c["id"] for c in (r.data or []))
    ids = [i for i in ids if i in valid]
    # "The day you select a party, ASVA starts counting from that day":
    # assigning a batch (re)anchors the overdue track to today, so an
    # already-overdue party gets a fresh polite start, not instant escalation.
    today = _today().isoformat()
    for chunk in _chunked(ids, 100):
        db.table("clients").update(
            {"reminder_batch": b, "reminder_anchor": today}).in_("id", chunk).execute()
    return {"ok": True, "assigned": len(ids), "batch": b}


class SettingsPayload(BaseModel):
    token: str
    weekly_off_day: Optional[int] = None   # Mon=0..Sun=6, or null = open 7 days
    blackout_dates: list[str] = []
    reminder_style: Optional[str] = None   # gentle | standard | firm
    reminder_hour: Optional[int] = None    # 0..23
    reminder_custom_line: Optional[str] = None
    msg_language: Optional[str] = None     # hinglish | english
    discount_pct: Optional[float] = None   # early-payment discount, 0-50
    send_on_holidays: Optional[bool] = None   # true = chase on the off day too
    default_credit_days: Optional[int] = None  # 30 | 45 | 60 ... fallback term


@router.post("/admin/settings")
def admin_settings(payload: SettingsPayload):
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
    # Only when the caller actually sent one: this used to default to "standard"
    # and write it on every save, so saving a holiday reset a shop's cadence.
    style = (biz.get("reminder_style") or "standard").lower()
    if payload.reminder_style is not None:
        style = payload.reminder_style.lower()
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

    if payload.send_on_holidays is not None:
        update["send_on_holidays"] = bool(payload.send_on_holidays)

    # The fallback credit period for parties Tally gives no BillCreditPeriod for.
    # Changing it does NOT touch a party whose term the owner set by hand, nor
    # one Tally reports a real term for - it only fills the blanks, on the next
    # refresh. Capped at a year: a "credit period" longer than that is a write-off.
    if payload.default_credit_days is not None:
        cd = int(payload.default_credit_days)
        if not (1 <= cd <= 365):
            raise HTTPException(status_code=400, detail="default_credit_days must be 1-365")
        update["default_credit_days"] = cd

    db.table("businesses").update(update).eq("id", biz["id"]).execute()

    return {
        "ok": True,
        "style": style,
        "weekly_off_day": woff,
        "reminder_hour": update.get("reminder_hour"),
        "blackout_count": len(clean),
        "discount_pct": update.get("discount_pct"),
        "send_on_holidays": update.get("send_on_holidays"),
        "default_credit_days": update.get("default_credit_days"),
    }


@router.get("/admin/preview")
def admin_preview(
    token: str = Query(...),
    style: str | None = Query(None),
    language: str = Query("english"),
    custom_line: str | None = Query(None),
    discount_pct: float = Query(0.0),
    upi: str = Query(""),
    batch: int = Query(0),
):
    """The exact reminder a customer would receive, composed by the code that
    actually sends it.

    This used to render the old `reminder_en` template with a raw upi:// link,
    which the sender stopped using when reminders moved to a tappable pay page.
    The owner was previewing a message their customers had not received in
    months. Now the sender composes it in dry-run, so the preview cannot drift
    from the send again: one body, one code path.

    The sample party and figures are fixed so the preview is about the wording,
    not about whoever happens to be top of the list.
    """
    import asyncio as _asyncio
    from decimal import Decimal

    from app.services import bot as bot_svc

    biz = _biz_by_token(token)
    lang = (language or "english").lower()

    # Start from the batch as SAVED, so everything the query does not mention
    # (its closing line, its collection method) is the real thing, then apply
    # whatever the owner has changed on screen but not saved yet.
    from app.services.batches import get_batches
    saved = get_batches(biz)
    base = dict(saved[batch]) if 0 <= batch < len(saved) else dict(saved[0])
    base["lang"] = lang
    if upi:
        base["upi"] = upi.strip()
    base["disc"] = discount_pct or 0

    sample_biz = dict(biz)
    if custom_line is not None:
        sample_biz["reminder_custom_line"] = custom_line
    if style:
        sample_biz["reminder_style"] = style
    sample_biz["reminder_batches"] = [base]

    today = _today()
    bill = {"id": "preview", "invoice_number": "2526RTC0203",
            "invoice_date": (today - _dt.timedelta(days=35)).isoformat(),
            "due_date": (today - _dt.timedelta(days=5)).isoformat(),
            "outstanding": Decimal("12500")}
    entry = {
        "client": {"id": "preview", "name": "Ramesh Traders",
                   "whatsapp_number": "919000000000", "reminder_batch": 0,
                   "language": "hi"},
        "total": Decimal("12500"),
        "bills": [bill],
    }

    try:
        text = _asyncio.run(bot_svc._send_consolidated_reminder(
            sample_biz, entry, dry_run=True))
    except Exception:
        log.exception("preview failed")
        return {"message": ""}
    return {"message": text or ""}


@router.post("/admin/send-now")
async def admin_send_now(payload: SendNowPayload):
    """Send a reminder to ONE party immediately (owner presses 'Send now').
    Reuses the bot's REMIND handler so behaviour matches the WhatsApp command."""
    from app.services import bot
    biz = _biz_by_token(payload.token)
    party = (payload.party or "").strip()
    if not party:
        raise HTTPException(status_code=400, detail="party required")

    # Strong pre-check: if the shop WhatsApp is known-disconnected (a recent
    # negative heartbeat), don't quietly queue a send that will just sit there -
    # tell the owner to reconnect first. Only trip on a FRESH negative reading so
    # a stale/never-checked flag can't block a legitimate send.
    if biz.get("wa_ready") is False and biz.get("wa_checked_at"):
        from datetime import datetime, timezone
        try:
            ts = datetime.fromisoformat(str(biz["wa_checked_at"]).replace("Z", "+00:00"))
            fresh = (datetime.now(timezone.utc) - ts).total_seconds() < 20 * 60
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return {"sent": False, "wa_down": True,
                    "detail": "Your shop WhatsApp is not connected, so this reminder cannot go out. "
                              "Open WhatsApp Setup, scan the QR to reconnect, then try Send now again."}
    business = {
        "id": biz["id"],
        "business_name": biz.get("business_name", ""),
        "plan": biz.get("plan", "starter"),
        "whatsapp_number": biz.get("whatsapp_number"),
        "upi_vpa": biz.get("upi_vpa"),
        "upi_vpa_2": biz.get("upi_vpa_2"),
        "upi_vpa_3": biz.get("upi_vpa_3"),
        "bank_account_name": biz.get("bank_account_name"),
        "bank_account_no": biz.get("bank_account_no"),
        "bank_ifsc": biz.get("bank_ifsc"),
        "bank_name": biz.get("bank_name"),
        "discount_pct": biz.get("discount_pct"),
        "msg_language": biz.get("msg_language"),
        "reminder_style": biz.get("reminder_style"),
        "reminder_custom_line": biz.get("reminder_custom_line"),
        "reminder_batches": biz.get("reminder_batches"),
    }
    try:
        reply = await bot._handle_remind(business, party)
    except Exception as e:
        log.exception("send-now failed for %s", party)
        return {"sent": False, "detail": str(e)[:80]}
    # Success = something actually went out (or was queued for the shop
    # number). A ❌ failure line (WhatsApp down, no number) is NOT success -
    # the dashboard must show the owner the real outcome.
    low = (reply or "").lower()
    ok = bool(reply) and "❌" not in reply and "nahi mila" not in low and "not found" not in low
    return {"sent": ok, "detail": reply[:160] if reply else ""}


class LetterPayload(BaseModel):
    token: str
    client_id: str


@router.post("/admin/letter-preview")
async def admin_letter_preview(payload: LetterPayload):
    """Show the EXACT formal reminder that would go to this party - no send. The
    owner reads it and decides; escalation is never automatic."""
    from app.services import bot
    biz = _biz_by_token(payload.token)
    business = {"id": biz["id"], "business_name": biz.get("business_name", ""),
               "plan": biz.get("plan", "starter")}
    try:
        return await bot.letter_for_client(business, payload.client_id, send=False)
    except Exception as e:
        log.exception("letter-preview failed for %s", payload.client_id)
        return {"ok": False, "detail": str(e)[:120]}


@router.post("/admin/send-letter")
async def admin_send_letter(payload: LetterPayload):
    """Send the owner-approved formal reminder to ONE party (the Escalate button).
    Owner-triggered only, after they have seen the preview."""
    from app.services import bot
    biz = _biz_by_token(payload.token)
    # Same fresh-WhatsApp-down guard as Send now: don't quietly queue if the shop
    # number is known-disconnected right now.
    if biz.get("wa_ready") is False and biz.get("wa_checked_at"):
        from datetime import datetime, timezone
        try:
            ts = datetime.fromisoformat(str(biz["wa_checked_at"]).replace("Z", "+00:00"))
            fresh = (datetime.now(timezone.utc) - ts).total_seconds() < 20 * 60
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return {"ok": False, "wa_down": True,
                    "detail": "Your shop WhatsApp is not connected, so this cannot go out. "
                              "Open WhatsApp Setup, scan the QR to reconnect, then try again."}
    business = {"id": biz["id"], "business_name": biz.get("business_name", ""),
               "plan": biz.get("plan", "starter")}
    try:
        return await bot.letter_for_client(business, payload.client_id, send=True)
    except Exception as e:
        log.exception("send-letter failed for %s", payload.client_id)
        return {"ok": False, "detail": str(e)[:120]}


class ConfirmBalancePayload(BaseModel):
    token: str
    client_id: str


@router.post("/admin/confirm-balance")
async def admin_confirm_balance(payload: ConfirmBalancePayload):
    """Owner asks ONE customer to confirm their outstanding ('sahi hai?'). This is
    the only ASVA message aimed at a customer other than bills/reminders, so it is
    deliberately owner-initiated (never automatic) and deduped to at most once per
    90 days per party - it must never feel like spam. The customer's reply lands
    in the owner's normal inbox view; ASVA does not auto-reply."""
    from app.services import whatsapp, templates
    from app.models import MessageType, Plan, Lang
    biz = _biz_by_token(payload.token)
    db = require_db()
    bid = biz["id"]
    cr = (db.table("clients")
          .select("id, name, whatsapp_number, reminder_batch, last_balance_confirm_at")
          .eq("id", payload.client_id).eq("business_id", bid).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    client = cr.data[0]
    if not client.get("whatsapp_number"):
        return {"ok": False, "reason": "no_number",
                "detail": "This customer has no WhatsApp number. Add one first."}

    # Dedup: never ask the same customer more than once in 90 days.
    last = client.get("last_balance_confirm_at")
    if last:
        try:
            when = _dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if (_dt.datetime.now(_dt.timezone.utc) - when).days < 90:
                return {"ok": False, "reason": "recent",
                        "detail": "Already asked this customer within the last 90 days."}
        except (TypeError, ValueError):
            pass

    outstanding = 0.0
    ob = (db.table("bills").select("outstanding")
          .eq("business_id", bid).eq("client_id", client["id"])
          .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    outstanding = float(sum(Decimal(str(b.get("outstanding") or 0)) for b in ob))
    if outstanding <= 0:
        return {"ok": False, "reason": "nothing_due",
                "detail": "This customer has no outstanding to confirm."}

    # Same language as this party's reminders (clients.language is a legacy
    # enum that is always 'hi', so it must not decide).
    from app.services.batches import resolve_batch
    en = resolve_batch(biz, client.get("reminder_batch"))["lang"] == "english"
    text = templates.balance_confirm_text(biz.get("business_name") or "", outstanding, en=en)
    try:
        res = await whatsapp.send_message(
            business_id=bid, to_number=client["whatsapp_number"], message_text=text,
            plan=Plan(biz.get("plan", "starter")), message_type=MessageType.balance_confirm,
            client_id=client["id"], language=Lang.hi, channel="shop")
    except Exception as e:
        log.exception("confirm-balance send failed for %s", client.get("name"))
        return {"ok": False, "reason": "send_failed", "detail": str(e)[:120]}
    sent = bool(res and res.get("sent") is not False)
    if sent:
        db.table("clients").update(
            {"last_balance_confirm_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
        ).eq("id", client["id"]).execute()
    return {"ok": sent, "sent": sent, "outstanding": outstanding,
            "detail": "Balance-confirm sent." if sent else "Could not send right now."}


class SetNumberPayload(BaseModel):
    token: str
    client_id: str
    number: str


@router.post("/admin/set-number")
def admin_set_number(payload: SetNumberPayload):
    """Owner adds/edits a customer's WhatsApp number in ASVA, for the many parties
    whose number was never stored in Tally. The owner types just the 10 digits; we
    normalise to 91XXXXXXXXXX (India). Tally still wins WHEN it has a number - a
    blank Tally field never overwrites this (see _sync_contacts), so the number the
    owner adds here sticks until Tally itself gets one."""
    from app.routers.tally import _normalize_phone
    biz = _biz_by_token(payload.token)
    db = require_db()
    phone = _normalize_phone(payload.number)
    if not phone:
        raise HTTPException(status_code=400,
                            detail="Enter a valid 10-digit Indian mobile (it must start with 6, 7, 8 or 9).")
    cr = (db.table("clients").select("id")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    db.table("clients").update({"whatsapp_number": phone}).eq("id", payload.client_id).execute()
    return {"ok": True, "number": phone}


@router.get("/admin/missing-numbers")
def admin_missing_numbers(token: str = Query(...)):
    """Worklist for problem #4: every party that OWES money but has no WhatsApp
    number, sorted by outstanding (biggest first). The owner knocks out the top
    debtors from their phone in a couple of minutes - that is what turns 'no
    numbers saved' into 'reminders actually go out'."""
    from app.services import names
    biz = _biz_by_token(token)
    db = require_db()
    bid = biz["id"]
    clients = (db.table("clients")
               .select("id, name, whatsapp_number, excluded")
               .eq("business_id", bid).execute()).data or []
    # Only parties with NO usable number (null OR blank) and not marked do-not-chase.
    nonum = {c["id"]: c for c in clients
             if not (c.get("whatsapp_number") or "").strip() and not c.get("excluded")}
    if not nonum:
        return {"parties": [], "count": 0}
    owed: dict = {}
    bills = (db.table("bills").select("client_id, outstanding")
             .eq("business_id", bid).in_("client_id", list(nonum.keys()))
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    for b in bills:
        cid = b.get("client_id")
        if cid in nonum:
            owed[cid] = owed.get(cid, 0.0) + float(b.get("outstanding") or 0)
    # Accurate + actionable: ONLY parties who actually OWE money (owed > 0). A
    # party with no number who owes nothing is not a problem to nag the owner about.
    out = [{"client_id": cid, "name": names.clean_display(nonum[cid].get("name") or "") or "Customer",
            "outstanding": round(amt, 2)} for cid, amt in owed.items() if amt > 0]
    out.sort(key=lambda x: x["outstanding"], reverse=True)
    return {"parties": out, "count": len(out)}


class ImportNumbersPayload(BaseModel):
    token: str
    text: str            # pasted lines: "Party Name, 98765 43210" (comma/space/tab)


def _parse_import_line(line: str):
    """(name, raw_number) from one pasted line, or None. The phone is the digit
    run at the END; everything before it is the name. Tolerates commas/tabs/dashes
    and a leading +91."""
    line = (line or "").strip()
    if not line:
        return None
    m = re.search(r'(\+?\d[\d\s\-]{8,}\d)\s*$', line)
    if not m:
        return None
    name = line[:m.start()].strip(" ,\t-|:")
    return (name, m.group(1)) if name else None


def _parse_vcard(text: str):
    """Contacts exported from a phone come as vCard (.vcf): iPhone 'Share contact',
    Android 'Export contacts'. Pull (FN, first TEL) from each card."""
    out, name, tel = [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        u = line.upper()
        if u.startswith("BEGIN:VCARD"):
            name, tel = None, None
        elif u.startswith("FN:"):
            name = line[3:].strip()
        elif u.startswith("N:") and not name:
            parts = line[2:].split(";")             # N:Last;First;;; -> "First Last"
            name = " ".join(p for p in [parts[1] if len(parts) > 1 else "",
                                        parts[0] if parts else ""] if p).strip() or None
        elif u.startswith("TEL") and ":" in line and not tel:
            tel = line.split(":", 1)[1].strip()
        elif u.startswith("END:VCARD"):
            if name and tel:
                out.append((name, tel))
            name, tel = None, None
    return out


def _parse_google_csv(text: str):
    """Google Contacts CSV export: a 'Name' (or First/Last) column + one or more
    'Phone N - Value' columns."""
    import csv
    import io
    out = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            name = (row.get("Name") or "").strip() or " ".join(
                p for p in [(row.get("First Name") or "").strip(),
                            (row.get("Last Name") or "").strip()] if p).strip()
            num = ""
            for k, v in row.items():
                if v and k and ("phone" in k.lower() or "mobile" in k.lower()) \
                        and "type" not in k.lower():
                    num = v.strip()
                    break
            if name and num:
                out.append((name, num))
    except Exception:
        log.warning("google-csv parse failed; falling back", exc_info=True)
    return out


def _parse_contacts(text: str):
    """Turn ANY common contact export into [(name, number)]: vCard (.vcf), Google
    CSV, or plain pasted 'Name  Number' lines. Auto-detected by content."""
    text = text or ""
    if "BEGIN:VCARD" in text.upper():
        return _parse_vcard(text)
    first = next((l for l in text.splitlines() if l.strip()), "").lower()
    if "," in first and "name" in first and ("phone" in first or "mobile" in first):
        rows = _parse_google_csv(text)
        if rows:
            return rows
    return [p for p in (_parse_import_line(l) for l in text.splitlines()) if p]


@router.post("/admin/import-numbers")
def admin_import_numbers(payload: ImportNumbersPayload):
    """Bulk-fill WhatsApp numbers by PASTING them from the owner's phone (where
    they already keep party numbers). Each line is matched to a Tally party by
    name; a valid number fills a party that has none. Returns what filled, what
    did not match, and what was a bad number - so nothing fails silently.
    Never overwrites a number Tally/ASVA already has (safe to re-run)."""
    from app.routers.tally import _normalize_phone
    biz = _biz_by_token(payload.token)
    db = require_db()
    bid = biz["id"]

    clients = (db.table("clients")
               .select("id, name, tally_ledger_name, whatsapp_number")
               .eq("business_id", bid).execute()).data or []

    def norm(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (s or "").lower())

    by_name: dict = {}
    for c in clients:
        for key in (c.get("name"), c.get("tally_ledger_name")):
            k = norm(key)
            if k:
                by_name.setdefault(k, []).append(c)

    filled, skipped_have, unmatched, bad_number = [], [], [], []
    seen_ids = set()
    for name, num in _parse_contacts(payload.text):
        phone = _normalize_phone(num)
        if not phone:
            bad_number.append(name)
            continue
        k = norm(name)
        cand = by_name.get(k)
        if not cand:                       # fall back to a UNIQUE contains-match
            hits = [c for kk, lst in by_name.items() for c in lst
                    if (k and (k in kk or kk in k))]
            uniq = {c["id"]: c for c in hits}
            cand = list(uniq.values()) if len(uniq) == 1 else None
        if not cand:
            unmatched.append(name)
            continue
        c = cand[0]
        if c["id"] in seen_ids:
            continue
        seen_ids.add(c["id"])
        if c.get("whatsapp_number"):
            skipped_have.append(c.get("name"))
            continue
        db.table("clients").update({"whatsapp_number": phone}).eq("id", c["id"]).execute()
        filled.append({"name": c.get("name"), "number": phone})

    return {"filled": filled, "filled_count": len(filled),
            "already_had": skipped_have, "unmatched": unmatched,
            "bad_number": bad_number}


class CreditDaysPayload(BaseModel):
    token: str
    client_id: str
    days: int


@router.post("/admin/set-credit-days")
def admin_set_credit_days(payload: CreditDaysPayload):
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


# ── Non-Tally bill management (add / edit / delete) ───────────────────
# Tally bills are owned by Tally and must never be touched here; these
# endpoints cover only WhatsApp-made bills (photo OCR + typed BILL) plus
# bills added right here on the dashboard (source=manual).

def _nt_bill_or_404(db, biz_id: str, bill_id: str) -> dict:
    """Fetch a bill and refuse anything Tally-owned."""
    r = (db.table("bills")
         .select("id, amount, paid_amount, status, invoice_number, invoice_date, source, client_id")
         .eq("id", bill_id).eq("business_id", biz_id).limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=404, detail="bill not found")
    bill = r.data[0]
    if (bill.get("source") or "tally") == "tally":
        raise HTTPException(status_code=400, detail="Tally bill - edit it in Tally, it syncs here")
    return bill


def _nt_status(amount: float, paid: float, due_date: str | None) -> str:
    """Recompute a non-Tally bill's status after an amount edit."""
    if paid >= amount - 0.01:
        return "paid"
    if paid > 0:
        return "partial"
    try:
        if due_date and _dt.date.fromisoformat(str(due_date)) < _today():
            return "overdue"
    except (TypeError, ValueError):
        pass
    return "pending"


def _parse_ymd(s: str | None, field: str) -> _dt.date | None:
    """Parse a 'YYYY-MM-DD' string, or None if empty. Raises 400 on bad format."""
    s = (s or "").strip()[:10]
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a date (YYYY-MM-DD)")


class NTBillAddPayload(BaseModel):
    token: str
    client_id: str
    amount: float
    invoice_number: str = ""
    credit_days: int | None = None
    invoice_date: str | None = None   # YYYY-MM-DD, default today
    due_date: str | None = None       # YYYY-MM-DD, optional (else invoice_date + credit days)


@router.post("/admin/nt-bill/add")
def admin_nt_bill_add(payload: NTBillAddPayload):
    """Add a non-Tally bill from the party page: choose the amount, the bill date,
    and (optionally) the due date. The same record the BILL command makes, but
    silent - no WhatsApp send."""
    import uuid as _uuid
    biz = _biz_by_token(payload.token)
    db = require_db()
    if not (0 < payload.amount <= 100_000_000):
        raise HTTPException(status_code=400, detail="amount must be positive")
    cr = (db.table("clients").select("id, credit_days")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    days = payload.credit_days or cr.data[0].get("credit_days") or 30
    if not (1 <= days <= 730):
        raise HTTPException(status_code=400, detail="credit days must be 1-730")
    inv_date = _parse_ymd(payload.invoice_date, "bill date") or _today()
    due = _parse_ymd(payload.due_date, "due date") or (inv_date + _dt.timedelta(days=days))
    if due < inv_date:
        raise HTTPException(status_code=400, detail="due date cannot be before the bill date")
    inv_no = (payload.invoice_number or "").strip()[:40] or f"NT-{_uuid.uuid4().hex[:6].upper()}"
    row = db.table("bills").insert({
        "business_id": biz["id"],
        "client_id": payload.client_id,
        "invoice_number": inv_no,
        "tally_voucher_number": f"TEXT-{_uuid.uuid4().hex[:12]}",
        "amount": round(float(payload.amount), 2),
        "paid_amount": 0.0,
        "invoice_date": inv_date.isoformat(),
        "due_date": due.isoformat(),
        "status": _nt_status(round(float(payload.amount), 2), 0.0, due.isoformat()),
        "source": "manual",
    }).execute()
    return {"ok": True, "bill_id": row.data[0]["id"], "invoice_number": inv_no}


class NTBillEditPayload(BaseModel):
    token: str
    bill_id: str
    amount: float | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None   # YYYY-MM-DD
    due_date: str | None = None       # YYYY-MM-DD


@router.post("/admin/nt-bill/edit")
def admin_nt_bill_edit(payload: NTBillEditPayload):
    """Edit a non-Tally bill's amount, bill number, bill date and/or due date.
    Status and the derived outstanding recompute automatically."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    bill = _nt_bill_or_404(db, biz["id"], payload.bill_id)
    # Current values (fetch once so status can be recomputed from the final state).
    cur = (db.table("bills").select("amount, paid_amount, invoice_date, due_date")
           .eq("id", bill["id"]).limit(1).execute()).data
    cur = cur[0] if cur else {}
    paid = float(cur.get("paid_amount") or 0)
    patch: dict = {}
    new_amt = float(cur.get("amount") or 0)
    if payload.amount is not None:
        new_amt = round(float(payload.amount), 2)
        if not (0 < new_amt <= 100_000_000):
            raise HTTPException(status_code=400, detail="amount must be positive")
        if new_amt < paid - 0.01:
            raise HTTPException(status_code=400,
                                detail=f"amount cannot be below already-paid ₹{paid:.0f}")
        patch["amount"] = new_amt
    if payload.invoice_number is not None:
        inv = payload.invoice_number.strip()[:40]
        if inv:
            patch["invoice_number"] = inv
    inv_d = _parse_ymd(payload.invoice_date, "bill date")
    due_d = _parse_ymd(payload.due_date, "due date")
    if inv_d:
        patch["invoice_date"] = inv_d.isoformat()
    if due_d:
        patch["due_date"] = due_d.isoformat()
    if not patch:
        raise HTTPException(status_code=400, detail="nothing to change")
    # Recompute status from the FINAL due date + amount (so an amount or date
    # change flips pending/overdue/paid correctly).
    final_due = patch.get("due_date", cur.get("due_date"))
    patch["status"] = _nt_status(new_amt, paid, final_due)
    db.table("bills").update(patch).eq("id", bill["id"]).execute()
    return {"ok": True, **patch}


class NTBillDeletePayload(BaseModel):
    token: str
    bill_id: str


@router.post("/admin/nt-bill/delete")
def admin_nt_bill_delete(payload: NTBillDeletePayload):
    """Delete a non-Tally bill (wrong OCR read, duplicate, test entry).
    Audit messages keep their rows (bill_id just becomes null)."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    bill = _nt_bill_or_404(db, biz["id"], payload.bill_id)
    db.table("bills").delete().eq("id", bill["id"]).execute()
    return {"ok": True, "deleted": bill.get("invoice_number") or bill["id"]}


# ── Non-Tally PARTY management (edit name/phone, delete whole party) ───
# Only for parties NOT synced from Tally. A Tally party (tally_ledger_name
# set) is owned by Tally - its name/number come from there, so editing it
# here would just be overwritten on the next sync. Photo/manual parties have
# no ledger name, so they are fully editable and deletable here.

def _nt_party_or_400(db, biz_id: str, client_id: str) -> dict:
    r = (db.table("clients")
         .select("id, name, whatsapp_number, tally_ledger_name")
         .eq("id", client_id).eq("business_id", biz_id).limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=404, detail="party not found")
    c = r.data[0]
    if (c.get("tally_ledger_name") or "").strip():
        raise HTTPException(status_code=400,
                            detail="Tally party - its name and number come from Tally.")
    return c


def _norm_phone_admin(raw: str | None) -> str | None:
    """'9876543210' / '+91 98765 43210' -> '919876543210'; None if not a valid
    Indian mobile. Empty string clears the number (returns None)."""
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits
    raise HTTPException(status_code=400,
                        detail="Enter a valid 10-digit mobile number, e.g. 9876543210")


class PartyEditPayload(BaseModel):
    token: str
    client_id: str
    name: str | None = None
    whatsapp_number: str | None = None   # "" clears it, missing = leave as-is


@router.post("/admin/party/edit")
def admin_party_edit(payload: PartyEditPayload):
    """Edit a NON-Tally party's name and/or WhatsApp number. Fixes a bad OCR
    read (e.g. the whole bill text saved as the name) or adds a missing phone."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    _nt_party_or_400(db, biz["id"], payload.client_id)
    patch: dict = {}
    if payload.name is not None:
        name = " ".join(payload.name.split()).strip()[:120]   # collapse newlines/spaces
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty.")
        patch["name"] = name
    if payload.whatsapp_number is not None:
        # "" clears; anything else must be a valid mobile.
        patch["whatsapp_number"] = (_norm_phone_admin(payload.whatsapp_number)
                                    if payload.whatsapp_number.strip() else None)
    if not patch:
        raise HTTPException(status_code=400, detail="nothing to change")
    db.table("clients").update(patch).eq("id", payload.client_id).execute()
    return {"ok": True, **patch}


class PartyCreatePayload(BaseModel):
    token: str
    name: str
    whatsapp_number: str | None = None
    credit_days: int | None = None


@router.post("/admin/party/create")
def admin_party_create(payload: PartyCreatePayload):
    """Create a brand-new NON-Tally party (a customer the shop tracks in ASVA
    only, not in Tally). No tally_ledger_name = non-Tally, so it is fully editable
    here and gets the same reminders + payment detection as any party."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    name = " ".join((payload.name or "").split()).strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="Enter the party name.")
    phone = (_norm_phone_admin(payload.whatsapp_number)
             if (payload.whatsapp_number or "").strip() else None)
    cd = payload.credit_days
    cd = int(cd) if (cd and 1 <= int(cd) <= 730) else DEFAULT_CREDIT_DAYS
    row = {
        "business_id": biz["id"],
        "name": name,
        "whatsapp_number": phone,
        "reminders_enabled": True,
        "credit_days": cd,          # NOT NULL column - never omit
    }
    ins = db.table("clients").insert(row).execute()
    return {"ok": True, "client_id": ins.data[0]["id"], "name": name}


class PartyDeletePayload(BaseModel):
    token: str
    client_id: str


@router.post("/admin/party/delete")
def admin_party_delete(payload: PartyDeletePayload):
    """Delete a NON-Tally party and all its bills (wrong OCR entry, duplicate,
    test). Message rows are kept for audit with their client_id cleared."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    party = _nt_party_or_400(db, biz["id"], payload.client_id)
    # Safety: never delete a party that still carries Tally bills (belt and
    # suspenders - a non-Tally party shouldn't have any, but check anyway).
    tally_bill = (db.table("bills").select("id")
                  .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
                  .eq("source", "tally").limit(1).execute())
    if tally_bill.data:
        raise HTTPException(status_code=400,
                            detail="This party has Tally bills - cannot delete here.")
    # Clear everything that points at this party FIRST, so a stale open receipt,
    # a promise-to-pay hold, or past replies can neither linger as orphans nor
    # block the delete. These tables key on client_id with no FK, so a plain
    # delete is safe; each is best-effort so one hiccup never aborts the whole
    # cleanup (this is the failsafe: a party ALWAYS deletes cleanly).
    for tbl in ("pending_receipts", "payment_promises", "inbound_messages"):
        try:
            db.table(tbl).delete().eq("client_id", payload.client_id).execute()
        except Exception:
            log.warning("cleanup of %s failed for party %s (continuing)",
                        tbl, payload.client_id, exc_info=True)
    # Detach audit messages (keep them for history), then delete bills + party.
    try:
        db.table("messages").update({"client_id": None}).eq("client_id", payload.client_id).execute()
    except Exception:
        log.exception("Detaching messages failed for party %s (continuing)", payload.client_id)
    try:
        db.table("bills").delete().eq("business_id", biz["id"]).eq("client_id", payload.client_id).execute()
        db.table("clients").delete().eq("id", payload.client_id).execute()
    except Exception:
        log.exception("Party delete failed for %s", payload.client_id)
        raise HTTPException(status_code=400, detail=(
            "Could not delete this party. If it still shows a pending payment or a "
            "paused reminder, clear that first, then try again."))
    return {"ok": True, "deleted": party.get("name") or payload.client_id}


class RecordPaymentPayload(BaseModel):
    token: str
    client_id: str
    amount: float
    payment_date: str | None = None   # YYYY-MM-DD, default today
    note: str | None = None


@router.post("/admin/record-payment")
async def admin_record_payment(payload: RecordPaymentPayload):
    """Record a payment against a NON-Tally party's open bills (FIFO, oldest
    first), update paid_amount + status, keep a dated entry in the payment log
    (manual_payments), and send the customer a 'received X, remaining Y'
    confirmation. Tally parties are excluded - their payments flow in from Tally
    automatically."""
    from decimal import Decimal
    from app.models import Lang, MessageType, Plan
    from app.services import whatsapp
    from app.services.templates import inr, render

    biz = _biz_by_token(payload.token)
    db = require_db()
    amount = round(float(payload.amount), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    pay_date = (_parse_ymd(payload.payment_date, "payment date") or _today()).isoformat()

    cr = (db.table("clients")
          .select("id, name, whatsapp_number, language, tally_ledger_name")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    client = cr.data[0]

    bills = (db.table("bills")
             .select("id, amount, paid_amount, status, invoice_date, source")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"])
             .order("invoice_date").execute()).data or []
    # NON-Tally bills only (leave Tally-synced bills to the Tally flow).
    # Discriminate by SOURCE, not voucher number: WhatsApp text bills carry a
    # synthetic TEXT-... voucher (dedup key), so 'empty voucher' misses them.
    bills = [b for b in bills if (b.get("source") or "tally") != "tally"]
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
        bupd = {"paid_amount": new_paid, "status": new_status}
        if new_status == "paid":
            bupd["settled_at"] = pay_date   # honest days-to-pay (migration 039)
        db.table("bills").update(bupd).eq("id", b["id"]).execute()
        remaining -= pay
        applied += pay

    # Keep a dated entry in the payment log so the party page shows real history.
    if applied > 0:
        try:
            db.table("manual_payments").insert({
                "business_id": biz["id"], "client_id": payload.client_id,
                "amount": round(applied, 2), "payment_date": pay_date,
                "note": (payload.note or "").strip()[:200] or None,
            }).execute()
        except Exception:
            log.exception("manual_payments log insert failed (payment still applied)")

    # Remaining outstanding for this party across its non-Tally open bills.
    after = (db.table("bills").select("amount, paid_amount, source, status")
             .eq("business_id", biz["id"]).eq("client_id", payload.client_id)
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    still_open = sum(float(b["amount"]) - float(b.get("paid_amount") or 0)
                     for b in after if (b.get("source") or "tally") != "tally")

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
            res = await whatsapp.send_message(
                business_id=biz["id"], to_number=client["whatsapp_number"],
                message_text=body, plan=Plan(biz.get("plan", "starter")),
                message_type=MessageType.payment_confirmation,
                client_id=client["id"], language=lang, channel="shop")
            # Honest flag: only true when it actually went (or was queued
            # for the shop number) - not when the send failed.
            confirmed = bool(res.get("sent") or res.get("queued"))
        except Exception:
            log.exception("non-Tally payment confirmation failed for %s", client.get("name"))

    return {
        "applied": round(applied, 2),
        "remaining_outstanding": round(still_open, 2),
        "unallocated": round(remaining, 2),
        "confirmation_sent": confirmed,
    }


class SetReminderPayload(BaseModel):
    token: str
    client_id: str
    enabled: bool


@router.post("/admin/set-reminder")
def admin_set_reminder(payload: SetReminderPayload):
    """Turn reminders ON/OFF for ONE party (per-party page toggle). Removal is
    confirmed client-side before this is called."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    cr = (db.table("clients").select("id, name")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    patch: dict = {"reminders_enabled": bool(payload.enabled)}
    if payload.enabled:
        # Turning ON = selection day: the overdue track restarts from today.
        patch["reminder_anchor"] = _today().isoformat()
    db.table("clients").update(patch).eq("id", payload.client_id).execute()
    return {"ok": True, "enabled": bool(payload.enabled)}


class SetExcludedPayload(BaseModel):
    token: str
    client_id: str
    excluded: bool


@router.post("/admin/set-excluded")
def admin_set_excluded(payload: SetExcludedPayload):
    """Move ONE party on/off the do-not-chase (excluded) list. Excluded parties
    get no reminders and no morning-checkpoint mention, and show only in the
    Do-not-chase tab (out of Tally/Non-Tally). Reversible."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    cr = (db.table("clients").select("id, name")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    db.table("clients").update({"excluded": bool(payload.excluded)}).eq(
        "id", payload.client_id).execute()
    return {"ok": True, "excluded": bool(payload.excluded)}


def _party_timeline(db, business_id: str, client_id: str, bills: list, receipts: list, esc) -> str:
    """One dated line per event, newest first, capped. Never raises: a bad
    row is skipped, an unavailable table is skipped, and an empty story
    renders nothing at all (no empty box on a fresh party)."""
    ev: list = []          # (sort_key_iso, css_kind, text)

    def add(when, kind, text):
        w = str(when or "")[:16].replace("T", " ")
        if w:
            ev.append((w, kind, text))

    for b in bills or []:
        try:
            add(b.get("invoice_date"), "bill",
                "Bill " + esc(b.get("invoice_number") or "-") + " for " + _inr(b.get("amount")))
        except Exception:
            continue
    for r in receipts or []:
        try:
            add(r.get("receipt_date"), "money", "Payment received in Tally " + _inr(r.get("amount"))
                + (" (" + esc(r["tally_voucher_number"]) + ")" if r.get("tally_voucher_number") else ""))
        except Exception:
            continue
    try:
        msgs = (db.table("messages").select("type, created_at, delivery_status, reminder_day, template_name")
                .eq("business_id", business_id).eq("client_id", client_id)
                .neq("type", "bot_reply").order("created_at", desc=True).limit(60).execute()).data or []
        # Cadence markers are bookkeeping (one per bill), not messages the customer got.
        msgs = [m for m in msgs if m.get("template_name") != "cadence_marker"]
        for m in msgs:
            t = m.get("type") or "message"
            st = m.get("delivery_status") or ""
            label = {"reminder": "Reminder sent", "invoice": "Invoice sent",
                     "owner_alert": "Letter sent", "balance_confirm": "Balance confirmation sent"}.get(t, t.replace("_", " ").capitalize())
            tick = (" (not delivered)" if st in ("failed", "limit_reached", "expired", "suspended")
                    else " (waiting to send)" if st == "queued"
                    else " (paused: customer promised)" if st == "held" else "")
            add(m.get("created_at"), "msg", label + tick)
    except Exception:
        pass
    try:
        from app.services import pay_signals as _ps
        for a_ in _ps.history_for_client(db, business_id, client_id, limit=12):
            if a_.get("kind") == "tapped":
                how = {"tally": "received in Tally", "screenshot": "customer sent proof",
                       "owner": "you recorded it", "dismissed": "not received",
                       "expired": "nothing seen in 3 days", "superseded": "tapped again"}
                txt = "Tapped Pay " + _inr(a_.get("amount")) + (" (" + esc(a_["note"]) + ")" if a_.get("note") else "")
                txt += " &rarr; " + (how.get(a_.get("resolved_by") or "", a_.get("resolved_by") or "") or "waiting")
                add(a_.get("created_at"), "tap", txt)
            else:
                add(a_.get("created_at"), "open", "Opened the payment link")
    except Exception:
        pass
    try:
        proms = (db.table("payment_promises").select("kind, promise_date, status, raw_text, created_at")
                 .eq("business_id", business_id).eq("client_id", client_id)
                 .order("created_at", desc=True).limit(10).execute()).data or []
        for p_ in proms:
            k = (p_.get("kind") or "promise").replace("_", " ")
            when = (" by " + str(p_.get("promise_date"))[:10]) if p_.get("promise_date") else ""
            st = p_.get("status") or "open"
            add(p_.get("created_at"), "promise",
                "Customer " + esc(k) + when + " (" + esc(st) + ")"
                + (": &ldquo;" + esc(str(p_.get("raw_text"))[:80]) + "&rdquo;" if p_.get("raw_text") else ""))
    except Exception:
        pass
    if not ev:
        return ""
    ev.sort(key=lambda e: e[0], reverse=True)
    rows = "".join(
        '<div class="tl-row"><span class="tl-when">' + esc(w[:10]) + '</span>'
        '<span class="tl-dot ' + k + '"></span><span class="tl-txt">' + t + "</span></div>"
        for w, k, t in ev[:40])
    more = ('<div class="tl-more">' + str(len(ev) - 40) + " earlier events not shown</div>") if len(ev) > 40 else ""
    return ('<h2>Story</h2><div class="tl">' + rows + more + "</div>")


@router.get("/admin/party", response_class=HTMLResponse)
def admin_party(token: str = Query(...), client_id: str = Query(...), lang: str = Query("english")):
    """Per-party page: bills, payments received, and the exact reminder schedule
    (which nudges already went, which is next and when) - all fetched from Tally."""
    biz = _biz_by_token(token)
    db = require_db()
    today = _today()

    cr = (db.table("clients")
          .select("id, name, whatsapp_number, credit_days, reminders_enabled, excluded, "
                  "tally_ledger_name, language, reminder_batch, reminder_anchor, created_at")
          .eq("id", client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    c = cr.data[0]
    cd_val = int(c.get("credit_days") or 0)
    is_tally = bool((c.get("tally_ledger_name") or "").strip())

    all_bills = (db.table("bills")
                 .select("id, invoice_number, amount, paid_amount, outstanding, invoice_date, "
                         "due_date, status, is_opening_balance, source")
                 .eq("business_id", biz["id"]).eq("client_id", client_id)
                 .order("invoice_date", desc=True).execute()).data or []
    open_bills = [b for b in all_bills if b["status"] in ("pending", "partial", "overdue")]
    open_total = sum(Decimal(str(b.get("outstanding") or 0)) for b in open_bills)

    # Payments recorded in Tally for this party (bill-wise history). A receipt row
    # may be linked to this party by client_id (rename-proof, migration 038) OR only
    # by the Tally ledger name (older rows, or a sync where the party did not match
    # by id). Matching by name ALONE missed a fresh receipt whenever the name
    # differed by a rename or a stray space - the exact bug where a new Tally payment
    # (e.g. 24 Aug) never showed while an older one (e.g. 20 May) still did. So we
    # query BOTH linkages and MERGE, dedup by voucher, newest first.
    receipts = []
    ledger_name = c.get("tally_ledger_name") or c.get("name")
    try:
        _seen = set()

        def _merge(rows):
            for r in rows or []:
                key = (r.get("tally_voucher_number"), str(r.get("receipt_date") or ""))
                if key in _seen:
                    continue
                _seen.add(key)
                receipts.append(r)

        _merge((db.table("tally_receipts")
                .select("amount, receipt_date, tally_voucher_number")
                .eq("business_id", biz["id"]).eq("client_id", client_id)
                .order("receipt_date", desc=True).limit(6).execute()).data)
        if ledger_name:
            _merge((db.table("tally_receipts")
                    .select("amount, receipt_date, tally_voucher_number")
                    .eq("business_id", biz["id"]).eq("party_name", ledger_name)
                    .order("receipt_date", desc=True).limit(6).execute()).data)
        # Only the latest few matter on the party page - newest first.
        receipts.sort(key=lambda r: str(r.get("receipt_date") or ""), reverse=True)
        receipts = receipts[:3]
    except Exception:
        receipts = []

    # Non-Tally payment log (recorded on this page) - the party's real history.
    manual_pays = []
    try:
        mp = (db.table("manual_payments")
              .select("amount, payment_date, note")
              .eq("business_id", biz["id"]).eq("client_id", client_id)
              .order("payment_date", desc=True).limit(3).execute())
        manual_pays = mp.data or []
    except Exception:
        manual_pays = []

    # Reminders already sent (bill_id, reminder_day) so the schedule shows ticks.
    sent_map: dict = {}
    try:
        mr = (db.table("messages")
              .select("bill_id, reminder_day, created_at, delivery_status")
              .eq("business_id", biz["id"]).eq("client_id", client_id)
              .eq("type", "reminder").limit(500).execute())
        for m in mr.data or []:
            sent_map[(m.get("bill_id"), m.get("reminder_day"))] = m
    except Exception:
        sent_map = {}

    def esc(s):
        return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ── The party's story: one vertical line, newest first ────────────
    # Bills, reminders sent, link opened, tapped Pay, promises, receipts and
    # letters, each dated. This is what the owner reads before a call, and it
    # is the evidence bundle a legal step will need, built for free.
    timeline_html = _party_timeline(db, biz["id"], client_id, all_bills, receipts, esc)

    # ── Bills table ───────────────────────────────────────────────────
    def attr(s) -> str:
        return esc(s).replace('"', "&quot;")

    OPEN = ("pending", "partial", "overdue")

    def _fmt_date(v) -> str:
        """2026-08-31 -> 31 Aug 2026. Owners read dates, not ISO stamps."""
        try:
            return _dt.date.fromisoformat(str(v)[:10]).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return esc(v) if v else "-"

    # One row per real invoice. Tally reaches us twice (day book + bill-wise
    # outstanding), and whatever leaves the outstanding list is marked paid
    # without clearing its balance - so a settled row that shadows an open row
    # is a duplicate of it, not a second bill, and a settled row's leftover
    # "outstanding" is stale and must never be shown as money owed.
    open_bills = [b for b in all_bills if b["status"] in OPEN]
    open_invoices = {str(b.get("invoice_number") or "").strip().lower()
                     for b in open_bills if b.get("invoice_number")}
    settled_bills = [
        b for b in all_bills
        if b["status"] not in OPEN
        and str(b.get("invoice_number") or "").strip().lower() not in open_invoices
    ]

    def _row(b, settled: bool) -> str:
        due_cell = _fmt_date(b.get("due_date"))
        if not settled and b.get("due_date"):
            try:
                late = (today - _dt.date.fromisoformat(str(b["due_date"])[:10])).days
                if late > 0:
                    due_cell += f' <span class="latechip">{late} din late</span>'
            except (TypeError, ValueError):
                pass
        non_tally = (b.get("source") or "tally") != "tally"
        src_tag = ""
        acts = ""
        if non_tally:
            src_tag = (' <span class="tag" style="background:#e1f3fe;color:#1f6c9f">'
                       + ("photo" if b.get("source") == "photo" else "WhatsApp") + "</span>")
            acts = (
                f'<button class="ntbtn" data-id="{b["id"]}" data-amt="{float(b.get("amount") or 0)}"'
                f' data-inv="{attr(b.get("invoice_number") or "")}"'
                f' data-date="{attr(str(b.get("invoice_date") or "")[:10])}"'
                f' data-due="{attr(str(b.get("due_date") or "")[:10])}" onclick="ntEdit(this)">Edit</button> '
                f'<button class="ntbtn ntdel" data-id="{b["id"]}"'
                f' data-inv="{attr(b.get("invoice_number") or "")}" onclick="ntDel(this)">Delete</button>'
            )
        paid_amt = float(b.get("paid_amount") or 0)
        if settled:
            # Closed in Tally: the balance is zero by definition, whatever the
            # stale column says, and "paid" is the whole story.
            paid_cell = _inr(paid_amt) if paid_amt > 0 else _inr(b.get("amount"))
            bal_cell = "-"
            status_cell = '<span class="tag ok">settled</span>'
        else:
            paid_cell = _inr(paid_amt) if paid_amt > 0 else "-"
            bal_cell = f'<b>{_inr(b.get("outstanding"))}</b>'
            stcls = "warn" if b["status"] == "partial" else "due"
            label = "part paid" if b["status"] == "partial" else "to collect"
            status_cell = f'<span class="tag {stcls}">{label}</span>'
        return (
            f'<tr><td>{esc(b.get("invoice_number") or "-")}{src_tag}</td>'
            f'<td>{_fmt_date(b.get("invoice_date"))}</td>'
            f'<td class="n">{_inr(b.get("amount"))}</td>'
            f'<td class="n">{paid_cell}</td>'
            f'<td class="n">{bal_cell}</td>'
            f'<td>{due_cell}</td>'
            f'<td>{status_cell}</td>'
            f'<td>{acts}</td></tr>'
        )

    bill_rows = "".join(_row(b, False) for b in open_bills[:200])
    if not bill_rows:
        bill_rows = ('<tr><td colspan="8" class="muted">Abhi koi bill baaki nahi.</td></tr>')

    settled_rows = "".join(_row(b, True) for b in settled_bills[:200])
    open_total = sum(Decimal(str(b.get("outstanding") or 0)) for b in open_bills)
    bills_summary = (
        f'{len(open_bills)} bill baaki &middot; <b>{_inr(open_total)}</b>'
        if open_bills else 'Sab clear. Koi bill baaki nahi.'
    )
    if settled_bills:
        bills_summary += f' &middot; {len(settled_bills)} settled'

    # ── Payments received ─────────────────────────────────────────────
    # Tally parties show their Tally receipts; non-Tally parties show the payment
    # LOG recorded on this page (manual_payments), with an amount + date + note.
    if is_tally:
        pay_rows = "".join(
            f'<tr><td>{esc(r.get("receipt_date"))}</td>'
            f'<td class="n">{_inr(r.get("amount"))}</td>'
            f'<td>{esc(r.get("tally_voucher_number") or "-")}</td></tr>'
            for r in receipts)
        if not pay_rows:
            pay_rows = '<tr><td colspan="3" class="muted">Tally me is party ka koi receipt record nahi mila.</td></tr>'
        payments_section = (
            '<div class="tablewrap"><table><tr><th>Date</th><th class="n">Amount</th><th>Voucher</th></tr>'
            + pay_rows + '</table></div>')
    else:
        pay_rows = "".join(
            f'<tr><td>{esc(str(r.get("payment_date") or "")[:10])}</td>'
            f'<td class="n">{_inr(r.get("amount"))}</td>'
            f'<td>{esc(r.get("note") or "-")}</td></tr>'
            for r in manual_pays)
        if not pay_rows:
            pay_rows = ('<tr><td colspan="3" class="muted">Abhi tak koi payment record nahi. '
                        'Upar &ldquo;Record payment&rdquo; se daalein.</td></tr>')
        payments_section = (
            '<div class="tablewrap"><table><tr><th>Date</th><th class="n">Amount</th><th>Note</th></tr>'
            + pay_rows + '</table></div>')

    # ── Pay-page activity: the funnel after a reminder. Opened -> tapped Pay
    # (amount, which bill) -> what became of it. Intent, never "paid".
    try:
        from app.services import pay_signals as _ps
        _acts = _ps.history_for_client(db, biz["id"], client_id)
    except Exception:
        _acts = []
    if _acts:
        _how = {"tally": "received in Tally", "screenshot": "customer sent proof",
                "owner": "you recorded it", "dismissed": "not received",
                "expired": "nothing seen in 3 days", "superseded": "tapped again"}
        act_rows = ""
        for a_ in _acts:
            when = str(a_.get("created_at") or "")[:16].replace("T", " ")
            if a_.get("kind") == "tapped":
                what = f'Tapped Pay {_inr(a_.get("amount"))}' + (f' ({esc(a_.get("note"))})' if a_.get("note") else "")
                if a_.get("resolved_by"):
                    what += f' <span class="muted">&rarr; {_how.get(a_["resolved_by"], a_["resolved_by"])}</span>'
                else:
                    what += ' <span class="muted">&rarr; waiting</span>'
            else:
                what = "Opened the payment link"
            act_rows += f"<tr><td>{esc(when)}</td><td>{what}</td></tr>"
        payments_section += ('<div class="muted" style="margin:12px 0 4px">Payment link activity</div>'
                             '<div class="tablewrap"><table><tr><th>When</th><th>What</th></tr>'
                             + act_rows + "</table></div>")

    # ── Reminder schedule: only meaningful once reminders are ON, and only
    # FORWARD from today. A party turned on AFTER the due date should see the
    # overdue messages it will now get, not a history of dates that never sent
    # (reminders were off then). Past points are never shown as "missed".
    from app.jobs.reminder_sweep import latest_reached_point
    rem_on = c.get("reminders_enabled", True)
    sent_count = len(sent_map)
    _KIND = {"overdue": "overdue", "escalate": "aapko alert", "nudge": "reminder", "predue": "reminder"}
    _RANK = {"nudge": 0, "predue": 1, "overdue": 2, "escalate": 3}
    goes_today = None            # a reached-but-unsent point -> fires next sweep
    upcoming: list = []          # (date, kind) strictly after today
    p_anchor = _client_anchor(c)
    if rem_on:
        for b in open_bills:
            inv, pts = _client_points(biz, b, cd_val, p_anchor)
            if not pts:
                continue
            dsi = (today - inv).days
            lrp = latest_reached_point(pts, dsi)
            if lrp and (b["id"], lrp[0]) not in sent_map:
                if goes_today is None or _RANK.get(lrp[1], 0) > _RANK.get(goes_today, 0):
                    goes_today = lrp[1]
            for day, kind in pts:
                d = inv + _dt.timedelta(days=day)
                if d > today:
                    upcoming.append((d, kind))
        upcoming = sorted(set(upcoming))[:8]

    def _rc(k):
        return "#c77b0a" if k in ("overdue", "escalate") else "#0a7d33"
    if not rem_on:
        next_label, next_color = "OFF", "#999"
    elif goes_today:
        next_label, next_color = f"Aaj ({_KIND[goes_today]})", _rc(goes_today)
    elif upcoming:
        d, k = upcoming[0]
        next_label, next_color = f"{d.strftime('%d %b')} ({_KIND[k]})", _rc(k)
    else:
        next_label, next_color = "Sab ho gaye", "#999"

    # ── Promise-to-Pay: why the next reminder may be later than usual ──
    # If the customer replied with a promise (or a paid claim), reminders are
    # paused. Show that plainly here AND override the "next reminder" card, so
    # the owner sees exactly why nothing is going out and can read what the
    # customer actually said.
    promise_html = ""
    try:
        from app.services import promises as _promises
        _pr = _promises.find_open(db, biz["id"], client_id)
    except Exception:
        _pr = None
    if _pr:
        _said = esc(_pr.get("raw_text") or "")
        _when = str(_pr.get("created_at") or "")[:10]
        _pdate = _pr.get("promise_date")
        _hold = str(_pr.get("hold_until") or "")[:10]
        _resume = str(_pdate) if _pdate else _hold
        if _pr.get("kind") == "paid_claim":
            _head = "Customer says they have already paid"
            _sub = f"Reminders are paused until {_hold}. Please confirm the payment in Tally."
        elif _pr.get("kind") == "promise" and _pdate:
            _head = f"Reminders paused until {_pdate}"
            _sub = "The customer promised to pay by then. ASVA resumes on its own if it stays unpaid."
        else:
            _head = f"Reminders paused until {_hold}"
            _sub = "ASVA resumes on its own if the bill stays unpaid."
        # The pause is the real answer to "when is the next reminder".
        if rem_on:
            next_label, next_color = (f"Paused &middot; {_resume}", "#7d5a0a")
        _saidline = (f'<div style="margin-top:8px">Customer said: '
                     f'<b>&ldquo;{_said}&rdquo;</b>{(" &middot; " + _when) if _when else ""}</div>') if _said else ""
        promise_html = (
            f'<div class="card" style="margin-bottom:18px;border-left:4px solid #7d5a0a;background:#fbf3db">'
            f'<div style="font-weight:700;color:#7d5a0a">&#9208;&nbsp; {_head}</div>'
            f'<div class="hint" style="color:#8a6a2a;margin-top:2px">{_sub}</div>'
            f'{_saidline}</div>')

    # Compact forward schedule (only when ON). Shows the TIME too - the owner
    # must always know exactly when the next message goes.
    sched_section = ""
    if rem_on:
        from app.services.batches import resolve_batch as _rb, batch_hour as _bh
        _sched_batch = _rb(biz, c.get("reminder_batch"))
        send_hh = f"{_bh(biz, _sched_batch):02d}:00"
        chips = ""
        if goes_today:
            chips += f'<span class="chip next" title="{_KIND[goes_today]}">Aaj {send_hh}</span>'
        for d, k in upcoming:
            chips += f'<span class="chip {"over" if k in ("overdue","escalate") else "due"}">{d.strftime("%d %b")}</span>'
        if not chips:
            chips = '<span class="muted">Sabhi reminder ja chuke.</span>'
        sched_section = (
            f'<div class="card" style="margin-top:0">'
            f'<div class="muted" style="margin-bottom:10px">Ab tak <b>{sent_count}</b> reminder gaye. '
            f'Roz bhejne ka time: <b>{send_hh}</b> ({esc(_sched_batch["name"])}). Aane wale:</div>'
            f'<div class="chips">{chips}</div></div>')

    # ── Past the cadence: the legal card ──────────────────────────────
    # Once the 300-day window is spent, another WhatsApp message is not the
    # instrument this debt needs. What it needs is a decision, and the clock
    # that decides is the three-year limitation period, not the days overdue.
    # So this card leads with the deadline, offers the draft, and - for a debt
    # already past three years - tells the owner to stop spending on it.
    from app.services import legal as _legal
    legal_html = legal_modals = ""
    lg = _legal.party_legal(open_bills, today)
    if lg:
        _lf = _legal_fields(db, biz["id"])
        _udyam = (_lf.get("udyam_number") or "").strip()
        _shop = names.clean_business(biz["business_name"])
        _dead = lg["state"] == _legal.TIME_BARRED
        _actionable = [b for b in open_bills
                       if (_legal.runway_days(b.get("due_date"), today) or 0) > 0
                       and (today - _dt.date.fromisoformat(str(b["due_date"])[:10])).days
                       > OVERDUE_WINDOW_DAYS] if not _dead else []
        _steps = "".join(
            f'<div class="lstep"><span class="ln">{n}</span><div>'
            f'<div class="lsh">{esc(h)}</div><div class="lst">{esc(t)}</div>'
            f'</div></div>'
            for n, (h, t) in enumerate(_legal.procedure(_udyam), 1))
        _notice = _legal.notice_text(
            shop=_shop, shop_address=_lf.get("shop_address"), party=c["name"],
            bills=_actionable, total=sum(Decimal(str(b.get("outstanding") or 0))
                                         for b in _actionable),
            udyam_number=_udyam, today=today, oldest_days=lg["oldest_days"]) if _actionable else ""

        # One sentence per state. Everything else is a button or a fold.
        _days = lg["oldest_days"]
        if _dead:
            _body = (f'<div><b>{_inr(lg["lost"])}</b> is more than 3 years old. The law no '
                     f'longer allows you to recover it. Write it off in Tally.</div>')
            _btns = ""
        else:
            _dl = lg["deadline"]
            if lg["state"] == _legal.SMALL:
                _line = (f'<div><b>{_inr(lg["amount"])}</b> is {_days} days overdue. A notice '
                         f'costs more than this is worth, so ask for any part payment.</div>')
            elif _dl:
                _line = (f'<div><b>{_inr(lg["amount"])}</b> is {_days} days overdue. After '
                         f'<b>{_dl.strftime("%d %b %Y")}</b> the law will not let you recover '
                         f'it.</div>')
            else:
                _line = (f'<div><b>{_inr(lg["amount"])}</b> is {_days} days overdue. WhatsApp '
                         f'reminders have stopped. A notice is the next step.</div>')
            _lost = (f'<div class="hint" style="margin-top:6px">Another '
                     f'<b>{_inr(lg["lost"])}</b> here is over 3 years old and cannot be '
                     f'recovered.</div>' if lg["lost"] else "")
            _body = _line + _lost
            _btns = (
                f'<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">'
                + ('<button class="danger" onclick="openEscalate()">Send final demand</button>'
                   if (c.get("whatsapp_number") and open_bills) else '')
                + ('<button class="ntbtn" onclick="openM(\'noticemodal\')">Notice draft</button>'
                   if _notice else '')
                + '<button class="ntbtn" onclick="openM(\'stepsmodal\')">What to do</button></div>')
        _udy_line = "" if (_udyam or _dead) else (
            f'<div class="hint" style="margin-top:10px">No Udyam number saved. With one you can '
            f'claim interest and file free on the MSME portal. '
            f'<a href="/admin/accounts?token={_h(token)}&amp;lang={_h(lang)}">Add it</a>.</div>')
        legal_html = (
            f'<div class="card lcard" style="border-left:4px solid {lg["color"]};'
            f'background:{"#f7f6f3" if _dead else "#fdecea"}">'
            f'<div class="lhead" style="color:{lg["color"]}">{esc(lg["label"])}</div>'
            f'{_body}{_btns}{_udy_line}</div>')
        legal_modals = (
            f'<div class="modal" id="stepsmodal" onclick="if(event.target===this)closeM(\'stepsmodal\')">'
            f'<div class="modalbox"><h3>What to do now</h3>'
            f'<div class="scroll">{_steps}'
            f'<details class="lmore"><summary>For your advocate</summary>'
            f'<div class="lst" style="margin-top:8px;white-space:pre-line">'
            f'{esc(_legal.procedure_detail(_udyam))}</div></details></div>'
            f'<div class="mbtns"><button class="ntbtn" '
            f'onclick="closeM(\'stepsmodal\')">Close</button></div></div></div>'
            + (f'<div class="modal" id="noticemodal" onclick="if(event.target===this)closeM(\'noticemodal\')">'
               f'<div class="modalbox" style="max-width:760px">'
               f'<h3>Notice draft</h3>'
               f'<div class="hint" style="margin:0 0 10px">Your advocate signs this and sends it '
               f'by registered post AD. ASVA does not send it.</div>'
               f'<div class="scroll"><pre id="noticetext" style="white-space:pre;overflow-x:auto;'
               f'background:#f7f6f3;border:1px solid #eaeaea;border-radius:8px;padding:12px;'
               f'font-size:.82rem;line-height:1.5;font-family:ui-monospace,Consolas,monospace;'
               f'margin:0">{esc(_notice)}</pre></div>'
               f'<div class="mbtns">'
               f'<button class="ntbtn" onclick="closeM(\'noticemodal\')">Close</button>'
               f'<button id="noticecopy" onclick="copyNotice()">Copy the notice</button>'
               f'</div></div></div>' if _notice else ''))

    # Past the cadence the KPI must stop promising a reminder there will never
    # be, and show the decision instead.
    next_kpi_l = "Agla reminder"
    if lg:
        next_label, next_color, next_kpi_l = lg["label"], lg["color"], "Next step"

    # Reminder batch selector (only meaningful when the shop has >1 batch).
    from app.services.batches import get_batches
    pbatches = get_batches(biz)
    cur_batch = int(c.get("reminder_batch") or 0)
    if cur_batch >= len(pbatches):
        cur_batch = 0
    if len(pbatches) > 1:
        bopts = ''.join(
            f'<option value="{i}"{" selected" if i == cur_batch else ""}>{i + 1}. {esc(b["name"])}</option>'
            for i, b in enumerate(pbatches))
        batch_html = (f'<div class="row" style="margin-top:10px">Batch: '
                      f'<select id="pbatch" onchange="assignBatch()">{bopts}</select> '
                      f'<span class="muted">(is party ke reminder ki language, UPI aur time)</span></div>')
    else:
        batch_html = ('<div class="row" style="margin-top:10px"><span class="muted">'
                      'Batch: 1 (Standard). Alag batches Reminders tab me banayein.</span></div>')

    phone = c.get("whatsapp_number")
    phone_html = esc(phone) if phone else '<span style="color:#c0392b">number nahi hai</span>'
    # The number is shown as text above (dial it on your phone). Add a WhatsApp
    # button: on the desktop app (no dialer) it is the way to reach the customer -
    # message or voice-call them from the PC; on a phone it still works.
    _wa_digits = re.sub(r"\D", "", phone or "")
    if len(_wa_digits) == 10:
        _wa_digits = "91" + _wa_digits
    phone_actions = ""
    if phone:
        phone_actions = (
            f' <a class="ntbtn" style="text-decoration:none" target="_blank" rel="noopener"'
            f' href="https://wa.me/{_wa_digits}">WhatsApp</a>')
    is_excl = bool(c.get("excluded"))
    toggle_label = "Reminder OFF karein" if rem_on else "Reminder ON karein"
    toggle_cls = "danger" if rem_on else "primary"
    src_tag = "Tally" if is_tally else "Non-Tally"
    rem_hint = ("ASVA is party ke credit days ke hisaab se khud reminder bhejta hai."
                if rem_on else
                "Reminders abhi OFF hain. ON karne par schedule niche dikhega.")

    # Non-Tally parties are fully editable (name, phone, delete). A Tally party's
    # name/number come from Tally, so we show a note instead of edit buttons.
    if is_tally:
        party_actions = ('<div class="muted" style="margin-top:8px">'
                         'Tally party - naam aur number Tally se aate hain.</div>')
    else:
        party_actions = ('<div class="pactions">'
                         '<button class="ntbtn" onclick="editParty()">Edit party</button>'
                         '<button class="ntbtn ntdel" onclick="delParty()">Delete party</button>'
                         '</div>')
    _ph = c.get("whatsapp_number") or ""
    pphone_display = _ph[2:] if (len(_ph) == 12 and _ph.startswith("91")) else _ph

    # ── Reliability scorecard (intra-shop, accurate) ──────────────────
    # Built only from provable data (terms-adjusted days past due, recorded
    # promise outcomes, real receipts). Explainable: the reasons are shown.
    from app.services import scorecard as _sc
    from app.services import risk as _risk
    scorecard_html = ""
    sc = None
    try:
        sc = _sc.build_scorecard(db, biz["id"], c, today=today)
        _sc_reasons = "".join(f"<li>{esc(r)}</li>" for r in sc.get("reasons", []))
        _sc_extra = ""
        if sc.get("total_recovered", 0) > 0:
            _sc_extra = (f'<div class="hint" style="margin-top:6px">Paid so far: '
                         f'<b>{_inr(sc["total_recovered"])}</b></div>')
        scorecard_html = (
            f'<div class="card" style="margin-bottom:18px;border-left:4px solid {sc["color"]}">'
            f'<div style="font-weight:700;color:{sc["color"]}">{sc["badge"]} '
            f'Reliability: {esc(sc["grade_label"])}</div>'
            f'<ul class="hint" style="margin:6px 0 0 18px;padding:0">{_sc_reasons}</ul>'
            f'{_sc_extra}</div>')
    except Exception:
        log.exception("scorecard render failed for %s (party page continues)", client_id)

    # Credit-risk advisory: a SINGLE calm line, only when caution is warranted, and
    # only here on the party page (never a list badge or a bill-time alert). Uses
    # the oldest open bill's overdue days + the reliability grade we already have.
    risk_html = ""
    try:
        _max_od = 0
        for _b in open_bills:
            _dd = _b.get("due_date")
            if _dd:
                try:
                    _max_od = max(_max_od, (today - _dt.date.fromisoformat(str(_dd)[:10])).days)
                except (TypeError, ValueError):
                    pass
        _rk = _risk.assess(open_total, _max_od, sc.get("grade") if sc else None)
        if _rk["advisory"]:
            _lv = _rk["level"]
            risk_html = (
                f'<div style="background:{_risk.BG.get(_lv, "#fbf3db")};'
                f'border:1px solid {_risk.BORDER.get(_lv, "#efdfa8")};color:{_risk.COLOR.get(_lv, "#7a5200")};'
                f'border-radius:8px;padding:10px 13px;margin-bottom:18px;font-size:.9rem;line-height:1.5">'
                f'{esc(_rk["advisory"])}</div>')
    except Exception:
        log.exception("risk advisory failed for %s (party page continues)", client_id)

    body = f"""
<a href="/admin?token={token}&lang={lang}" class="back">&#8592; Dashboard</a>
<h1>{esc(c['name'])}</h1>
<div class="muted">{src_tag} party &middot; {biz['business_name']}</div>
{party_actions}

<div class="grid">
  <div class="card kpi"><div class="n">{_inr(open_total)}</div><div class="l">Total baaki</div></div>
  <div class="card kpi"><div class="n">{len(open_bills)}</div><div class="l">Open bills</div></div>
  <div class="card kpi"><div class="n">{cd_val or '-'}</div><div class="l">Credit days</div></div>
  <div class="card kpi"><div class="n" style="font-size:1rem;color:{next_color}">{next_label}</div><div class="l">{next_kpi_l}</div></div>
</div>

{legal_html}

{scorecard_html}

{risk_html}

{promise_html}

<div class="card" style="margin-bottom:18px">
  <div class="remrow">
    <div>WhatsApp: <b>{phone_html}</b>{phone_actions} &nbsp;&middot;&nbsp; Reminder:
      <b id="remstate" style="color:{'#0a7d33' if rem_on else '#c0392b'}">{'ON' if rem_on else 'OFF'}</b></div>
    <button id="remtoggle" class="{toggle_cls}" onclick="toggleRem()">{toggle_label}</button>
  </div>
  <div class="hint" id="remhint">{rem_hint}</div>
  <div class="remrow" style="margin-top:12px;padding-top:12px;border-top:1px solid #eee">
    <div>Do-not-chase list: <b style="color:{'#8a6100' if is_excl else '#6b7770'}">{'YES - never chased' if is_excl else 'no'}</b>
      <div class="hint">{'This party is excluded: no reminders, and not shown in your chase list.' if is_excl else 'If this party will never pay, exclude them so they stop appearing in your chase list.'}</div></div>
    <button id="excltoggle" class="{'primary' if is_excl else 'danger'}" onclick="toggleExcl()">{'Bring back' if is_excl else 'Exclude'}</button>
  </div>
  {'''<div class="remrow" style="margin-top:12px;padding-top:12px;border-top:1px solid #eee">
    <div>Confirm balance with customer
      <div class="hint">Ask this customer to confirm their outstanding. Sent once (not more than every 90 days), only when you press it. Their reply comes to you; ASVA does not auto-reply.</div></div>
    <button id="cfbal" class="ntbtn" onclick="confirmBalance()">Ask to confirm</button>
  </div>''' if phone else ''}
  {f'''<div class="remrow" style="margin-top:12px;padding-top:12px;border-top:1px solid #eee">
    <div>Send a formal reminder
      <div class="hint">A firm, businesslike letter for a customer who is well past due. You see the exact message first and decide. ASVA never sends this on its own and never gets harsher by itself.</div></div>
    <button id="escbtn" class="danger" onclick="openEscalate()">Send formal reminder</button>
  </div>''' if (phone and open_bills) else ''}
  {batch_html}
</div>

<div class="modal" id="escmodal" onclick="if(event.target===this)closeM('escmodal')">
  <div class="modalbox">
    <h3>Send a formal reminder</h3>
    <div class="hint" style="margin:0 0 12px">A firmer, one-time letter for a customer who is well past due. You send it; ASVA never does this on its own.</div>
    <div class="scroll">
      <div id="escmeta" style="font-weight:700;color:#1d2420;font-size:.95rem;margin-bottom:8px"></div>
      <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#9a9a94;margin-bottom:5px">This exact message will be sent</div>
      <div id="escpreview" style="white-space:pre-wrap;background:#f7f6f3;border:1px solid #eaeaea;border-radius:8px;padding:12px;font-size:.9rem;line-height:1.5"></div>
      <div class="hint" style="margin-top:10px">Sent once, from your own WhatsApp number, to this customer only. ASVA does not repeat it or get harsher on its own. Their reply comes straight to you.</div>
    </div>
    <div class="mbtns" style="margin-top:16px;flex-shrink:0">
      <button class="ntbtn" onclick="closeM('escmodal')">Cancel</button>
      <button id="escsend" class="danger" onclick="sendLetter()">Send this letter</button>
    </div>
  </div>
</div>

{legal_modals}

{timeline_html}

<h2>Bills <button class="ntbtn ntadd" onclick="ntAdd()">+ Add bill</button>{'' if is_tally else ' <button class="ntbtn ntpay" onclick="ntPay()">₹ Record payment</button>'}</h2>
<div class="muted" style="margin:-4px 0 10px">{bills_summary}</div>
<div class="tablewrap"><table><tr><th>Bill</th><th>Date</th><th class="n">Amount</th><th class="n">Paid</th>
  <th class="n">Baaki</th><th>Due</th><th>Status</th><th></th></tr>{bill_rows}</table></div>
{f'<details class="foldsec"><summary>Settled bills ({len(settled_bills)})</summary><div class="hint" style="margin:0 0 10px">Already cleared in Tally. Kept for the record.</div><div class="tablewrap"><table><tr><th>Bill</th><th>Date</th><th class="n">Amount</th><th class="n">Paid</th><th class="n">Baaki</th><th>Due</th><th>Status</th><th></th></tr>{settled_rows}</table></div></details>' if settled_bills else ''}

{f'<details class="foldsec"><summary>Reminder schedule</summary>{sched_section}</details>' if sched_section else ''}

<details class="foldsec"><summary>Payment history</summary>{payments_section}</details>

<div class="modal" id="billmodal" onclick="if(event.target===this)closeM('billmodal')">
  <div class="modalbox">
    <h3 id="bm_title">Add bill</h3>
    <label>Amount (Rs)</label>
    <input id="bm_amt" type="text" inputmode="numeric" placeholder="12500">
    <label>Bill date</label>
    <input id="bm_date" type="date">
    <label>Due date (optional - else credit days)</label>
    <input id="bm_due" type="date">
    <label>Bill number (leave empty = automatic)</label>
    <input id="bm_inv" type="text" placeholder="e.g. INV-105">
    <div class="mbtns">
      <button class="ntbtn" onclick="closeM('billmodal')">Cancel</button>
      <button id="bm_save">Save</button>
    </div>
  </div>
</div>

<div class="modal" id="ntpaymodal" onclick="if(event.target===this)closeM('ntpaymodal')">
  <div class="modalbox">
    <h3>Record payment</h3>
    <div class="hint" style="margin:0 0 6px">Applied to the oldest open bills first.</div>
    <label>Amount received (Rs)</label>
    <input id="np_amt" type="text" inputmode="numeric" placeholder="10000">
    <label>Payment date</label>
    <input id="np_date" type="date">
    <label>Note (optional)</label>
    <input id="np_note" type="text" placeholder="e.g. cash / UPI / cheque">
    <div class="mbtns">
      <button class="ntbtn" onclick="closeM('ntpaymodal')">Cancel</button>
      <button id="np_save">Save</button>
    </div>
  </div>
</div>

<div class="modal" id="partymodal" onclick="if(event.target===this)closeM('partymodal')">
  <div class="modalbox">
    <h3>Edit party</h3>
    <label>Party name</label>
    <input id="pm_name" type="text" maxlength="120" placeholder="Ramesh Traders">
    <label>WhatsApp number (10 digits, leave empty = none)</label>
    <input id="pm_phone" type="text" inputmode="numeric" placeholder="9876543210">
    <div class="mbtns">
      <button class="ntbtn" onclick="closeM('partymodal')">Cancel</button>
      <button id="pm_save">Save</button>
    </div>
  </div>
</div>

<script>
const TOKEN = {token!r};

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

const CID = {client_id!r};
const LANG = {lang!r};
const PNAME = {json.dumps(c["name"] or "")};
const PPHONE = {json.dumps(pphone_display)};
let REM_ON = {str(bool(rem_on)).lower()};
let EXCL = {str(bool(is_excl)).lower()};
async function toggleExcl() {{
  const want = !EXCL;
  const msg = want ? PNAME + ' ko do-not-chase list me daalein? Reminders band ho jayenge.'
                   : PNAME + ' ko wapas chase list me laayein?';
  if (!confirm(msg)) return;
  const btn = document.getElementById('excltoggle'); btn.disabled = true;
  try {{
    const r = await fetch('/admin/set-excluded', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID, excluded: want}})}});
    if (r.ok) {{ location.reload(); return; }}
  }} catch (e) {{}}
  btn.disabled = false;
}}
function closeM(id) {{ document.getElementById(id).classList.remove('show'); }}
// Copy the demand-notice draft so it can be pasted into WhatsApp or email for
// the advocate. The clipboard API is blocked in some embedded webviews, so when
// it fails we select the text instead and say "press Ctrl+C" - the owner is
// never left with a button that silently did nothing.
function openM(id) {{ document.getElementById(id).classList.add('show'); }}
function copyNotice() {{
  const pre = document.getElementById('noticetext');
  const btn = document.getElementById('noticecopy');
  const done = (m) => {{ btn.textContent = m; setTimeout(() => {{ btn.textContent = 'Copy the notice'; }}, 2500); }};
  try {{
    navigator.clipboard.writeText(pre.textContent).then(() => done('Copied'), () => {{
      const r = document.createRange(); r.selectNodeContents(pre);
      const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      done('Selected - press Ctrl+C');
    }});
  }} catch (e) {{ done('Select the text and press Ctrl+C'); }}
}}

// Escalate = owner-approved formal reminder to THIS party. Preview first, then
// the owner decides. ASVA never sends this on its own.
async function openEscalate() {{
  const b = document.getElementById('escbtn');
  if (b) {{ b.disabled = true; b.textContent = 'Preparing...'; }}
  let d = null;
  try {{
    const r = await fetch('/admin/letter-preview', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID}})}});
    d = await r.json();
  }} catch (e) {{ d = null; }}
  if (b) {{ b.disabled = false; b.textContent = 'Send formal reminder'; }}
  if (!d || !d.ok) {{ alert((d && d.detail) || 'Could not prepare the letter.'); return; }}
  document.getElementById('escmeta').textContent =
    'To ' + d.name + '  \\u00b7  \\u20b9' + d.amount + ' outstanding'
    + (d.days_overdue > 0 ? ('  \\u00b7  ' + d.days_overdue + ' days overdue') : '');
  document.getElementById('escpreview').textContent = d.text;
  const s = document.getElementById('escsend');
  if (!d.has_number) {{ s.disabled = true; s.textContent = 'No WhatsApp number'; }}
  else {{ s.disabled = false; s.textContent = 'Send formal reminder'; }}
  openM('escmodal');
}}
async function sendLetter() {{
  const s = document.getElementById('escsend');
  s.disabled = true; s.textContent = 'Sending...';
  let d = null;
  try {{
    const r = await fetch('/admin/send-letter', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID}})}});
    d = await r.json();
  }} catch (e) {{ d = null; }}
  if (!d || !d.ok) {{
    s.disabled = false; s.textContent = 'Send formal reminder';
    alert((d && d.detail) || 'Could not send right now.'); return;
  }}
  closeM('escmodal');
  alert(d.detail || 'Formal reminder sent.');
}}
function ntNum(s) {{
  const a = parseFloat(String(s).replace(/[,₹\\s]/g, ''));
  return (a > 0) ? a : null;
}}
// POST helper: reload on success, show the server's real reason on failure.
async function ntPost(url, body) {{
  try {{
    const r = await fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(body)}});
    if (r.ok) {{ location.reload(); return; }}
    const d = await r.json().catch(() => ({{}}));
    alert(d.detail || 'Could not save. Please try again.');
  }} catch (e) {{ alert('Could not save. Please try again.'); }}
}}
async function assignBatch() {{
  const sel = document.getElementById('pbatch'); if (!sel) return;
  sel.disabled = true;
  try {{
    const r = await fetch('/admin/assign-batch', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_ids: [CID], batch: parseInt(sel.value)}})}});
    if (r.ok) location.reload(); else alert('Batch assign fail');
  }} catch (e) {{ alert('Batch assign fail'); }}
  sel.disabled = false;
}}
async function toggleRem() {{
  const turningOff = REM_ON;
  if (turningOff && !confirm(PNAME + ' ke reminder OFF kar dein? Isko automatic reminder nahi jayega.')) return;
  const btn = document.getElementById('remtoggle'); btn.disabled = true; btn.textContent = '...';
  try {{
    const r = await fetch('/admin/set-reminder', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID, enabled: !REM_ON}})}});
    if (r.ok) {{ location.reload(); return; }}   // reload so the schedule shows/hides
    alert('Nahi ho paya.');
  }} catch (e) {{ alert('Nahi ho paya.'); }}
  btn.disabled = false; btn.textContent = REM_ON ? 'Reminder OFF karein' : 'Reminder ON karein';
}}
// ── Non-Tally bill add / edit (in-page modal - prompt() is unusable in the app) ──
let BM_MODE = 'add', BM_ID = null;
function _today() {{ return new Date().toISOString().slice(0, 10); }}
function ntAdd() {{
  BM_MODE = 'add'; BM_ID = null;
  document.getElementById('bm_title').textContent = 'Add bill';
  document.getElementById('bm_amt').value = '';
  document.getElementById('bm_inv').value = '';
  document.getElementById('bm_date').value = _today();
  document.getElementById('bm_due').value = '';
  openM('billmodal');
  setTimeout(() => document.getElementById('bm_amt').focus(), 40);
}}
function ntEdit(btn) {{
  BM_MODE = 'edit'; BM_ID = btn.dataset.id;
  document.getElementById('bm_title').textContent = 'Edit bill';
  document.getElementById('bm_amt').value = btn.dataset.amt;
  document.getElementById('bm_inv').value = btn.dataset.inv;
  document.getElementById('bm_date').value = btn.dataset.date || '';
  document.getElementById('bm_due').value = btn.dataset.due || '';
  openM('billmodal');
  setTimeout(() => document.getElementById('bm_amt').focus(), 40);
}}
async function saveBill() {{
  const a = ntNum(document.getElementById('bm_amt').value);
  if (!a) {{ alert('Enter numbers only, e.g. 12500'); return; }}
  const inv = document.getElementById('bm_inv').value.trim();
  const date = document.getElementById('bm_date').value;
  const due = document.getElementById('bm_due').value;
  if (BM_MODE === 'add') {{
    await ntPost('/admin/nt-bill/add', {{token: TOKEN, client_id: CID, amount: a, invoice_number: inv, invoice_date: date, due_date: due}});
  }} else {{
    await ntPost('/admin/nt-bill/edit', {{token: TOKEN, bill_id: BM_ID, amount: a, invoice_number: inv, invoice_date: date, due_date: due}});
  }}
}}
document.getElementById('bm_save').onclick = saveBill;
// ── Record a payment (non-Tally): amount + date + note, applied FIFO ──
function ntPay() {{
  document.getElementById('np_amt').value = '';
  document.getElementById('np_date').value = _today();
  document.getElementById('np_note').value = '';
  openM('ntpaymodal');
  setTimeout(() => document.getElementById('np_amt').focus(), 40);
}}
async function saveNTPayment() {{
  const a = ntNum(document.getElementById('np_amt').value);
  if (!a) {{ alert('Enter the amount, e.g. 10000'); return; }}
  const date = document.getElementById('np_date').value;
  const note = document.getElementById('np_note').value.trim();
  const btn = document.getElementById('np_save'); btn.disabled = true;
  try {{
    const r = await fetch('/admin/record-payment', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID, amount: a, payment_date: date, note: note}})}});
    const d = await r.json().catch(() => ({{}}));
    if (r.ok && d.applied > 0) {{ location.reload(); return; }}
    alert(d.detail || 'No open bills to apply this payment to.');
  }} catch (e) {{ alert('Could not save. Please try again.'); }}
  btn.disabled = false;
}}
const _npSave = document.getElementById('np_save'); if (_npSave) _npSave.onclick = saveNTPayment;
async function ntDel(btn) {{
  if (!confirm('Delete bill ' + (btn.dataset.inv || '') + '? This cannot be undone.')) return;
  // Say it is happening: the page reloads on success and that takes a moment,
  // and a Delete button that does not react reads as a broken one.
  btn.disabled = true; btn.textContent = 'Deleting...';
  await ntPost('/admin/nt-bill/delete', {{token: TOKEN, bill_id: btn.dataset.id}});
  btn.disabled = false; btn.textContent = 'Delete';   // only reached if it failed
}}
// ── Non-Tally party edit / delete ──
function editParty() {{
  document.getElementById('pm_name').value = PNAME;
  document.getElementById('pm_phone').value = PPHONE;
  openM('partymodal');
  setTimeout(() => document.getElementById('pm_name').focus(), 40);
}}
async function saveParty() {{
  const name = document.getElementById('pm_name').value.trim();
  const phone = document.getElementById('pm_phone').value.trim();
  if (!name) {{ alert('Enter the party name.'); return; }}
  await ntPost('/admin/party/edit', {{token: TOKEN, client_id: CID, name: name, whatsapp_number: phone}});
}}
async function confirmBalance() {{
  const b = document.getElementById('cfbal'); if (!b) return;
  if (!confirm('Send a balance-confirm message to this customer now?')) return;
  b.disabled = true; b.textContent = 'Sending...';
  let d = {{}};
  try {{
    const r = await fetch('/admin/confirm-balance', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID}})}});
    d = await r.json().catch(() => ({{}}));
  }} catch (e) {{ d = {{}}; }}
  b.disabled = false;
  if (d && d.ok) {{ b.textContent = 'Sent \\u2713'; }}
  else {{ b.textContent = 'Ask to confirm'; alert((d && d.detail) || 'Could not send right now.'); }}
}}
const _pmSave = document.getElementById('pm_save'); if (_pmSave) _pmSave.onclick = saveParty;
async function delParty() {{
  if (!confirm('Delete party "' + PNAME + '" and ALL its bills? This cannot be undone.')) return;
  const btn = document.querySelector('.ntdel');
  if (btn) {{ btn.disabled = true; btn.textContent = 'Deleting...'; }}
  const undo = () => {{ if (btn) {{ btn.disabled = false; btn.textContent = 'Delete party'; }} }};
  try {{
    const r = await fetch('/admin/party/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, client_id: CID}})}});
    if (r.ok) {{ location.href = '/admin?token=' + encodeURIComponent(TOKEN) + '&lang=' + encodeURIComponent(LANG); return; }}
    const d = await r.json().catch(() => ({{}}));
    undo(); alert(d.detail || 'Could not delete. Please try again.');
  }} catch (e) {{ undo(); alert('Could not delete. Please try again.'); }}
}}
</script>"""
    extra = """
 .back{display:inline-block;margin-bottom:10px;color:#1f6c9f;text-decoration:none;font-weight:600}
 .remrow{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
 .tablewrap{overflow-x:auto}
 .foldsec{border:1px solid #EAEAEA;border-radius:12px;background:#fff;margin:14px 0;overflow:hidden}
 .foldsec>summary{cursor:pointer;list-style:none;padding:16px 20px;font-weight:700;font-size:1.05rem;display:flex;justify-content:space-between;align-items:center}
 .foldsec>summary::-webkit-details-marker{display:none}
 .foldsec>summary::after{content:"+";color:#0a7d33;font-weight:800;font-size:1.2rem}
 .foldsec[open]>summary::after{content:"\2212"}
 .foldsec[open]>summary{border-bottom:1px solid #EAEAEA}
 .foldsec>*:not(summary){margin:14px 20px}
 .foldsec .tablewrap,.foldsec .card{margin:14px 20px}
 .ntbtn{border:1px solid #EAEAEA;background:#fff;color:#2F3437;border-radius:6px;padding:4px 10px;font-size:.85em;cursor:pointer;white-space:nowrap}
 .ntbtn:hover{background:#f6f6f4}
 .ntbtn.ntdel{color:#fff;background:#c0392b;border-color:#c0392b;font-weight:600}
 .ntbtn.ntdel:hover{background:#a93226}
 .ntbtn.ntadd{margin-left:10px;font-weight:600;color:#0a7d33;vertical-align:middle}
 .ntbtn.ntpay{margin-left:8px;font-weight:600;color:#0a7d33;vertical-align:middle}
 .pactions{display:flex;gap:8px;margin:12px 0 0;flex-wrap:wrap}
 .pactions .ntbtn{padding:7px 14px;font-size:.9em}
 .modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:20;padding:16px}
 .modal.show{display:flex}
 .modalbox{background:#fff;max-width:460px;width:100%;border-radius:14px;padding:20px 22px;box-sizing:border-box;max-height:88vh;display:flex;flex-direction:column;overflow:hidden}
 .modalbox h3{margin:0 0 4px}
 .modalbox>.scroll{overflow-y:auto;margin:0 -22px;padding:0 22px;min-height:0}
 .modalbox label{display:block;font-size:.78rem;color:#787774;margin:14px 0 5px;text-transform:uppercase;letter-spacing:.04em}
 .modalbox input{font:inherit;padding:11px 12px;border:1px solid #EAEAEA;border-radius:8px;width:100%;box-sizing:border-box}
 .mbtns{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}
 /* The legal card: one heading, one sentence, one row of buttons. */
 .lcard{margin-bottom:18px}
 .lhead{font-weight:700;font-size:1.05rem;margin-bottom:5px}
 .lstep{display:flex;gap:11px;padding:11px 0;border-top:1px solid #F0F0EE}
 .lstep:first-child{border-top:0;padding-top:2px}
 .ln{flex:none;width:23px;height:23px;border-radius:999px;background:#eaf7ee;color:#0a5d28;
   font-weight:800;font-size:.78rem;display:flex;align-items:center;justify-content:center;margin-top:2px}
 .lsh{font-weight:700;font-size:.97rem;color:#2F3437}
 .lst{color:#787774;font-size:.88rem;line-height:1.5;margin-top:2px}
 .lmore{margin-top:14px;border-top:1px solid #F0F0EE;padding-top:12px}
 .lmore summary{cursor:pointer;color:#1f6c9f;font-size:.88rem}
 .tag{font-size:.75rem;border-radius:9999px;padding:2px 9px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
 .tag.ok{background:#edf3ec;color:#346538}
 .tag.warn{background:#fbf3db;color:#956400}
 .tag.due{background:#fdebec;color:#9f2f2d}
 .chips{display:flex;gap:6px;flex-wrap:wrap}
 .chip{font-size:.8rem;border-radius:6px;padding:3px 9px;border:1px solid #e2e2e0;color:#787774;white-space:nowrap}
 .chip.next{background:#e1f3fe;color:#1f6c9f;border-color:#bfe2f7;font-weight:700}
 .chip.due{background:#edf3ec;color:#346538;border-color:#cfe3cd}
 .chip.over{background:#fbf3db;color:#956400;border-color:#f0dfa8}
 button.danger{background:#c0392b}
 button.primary{background:#0a7d33}
 .latechip{display:inline-block;margin-left:6px;font-size:.72rem;font-weight:700;padding:1px 7px;border-radius:9999px;background:#FDEBEC;color:#9F2F2D}
"""
    nav = _topnav(token, lang, "dashboard",
                  self_url=f"/admin/party?client_id={client_id}")
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>{esc(biz.get("business_name") or "ASVA")} - {esc(c["name"])}</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}{extra}{_TL_CSS}</style><div class="wrap">{nav}{body}</div>',
        lang))


# ── Shared minimalist styling for the Analytics / Accounts pages ──────────
_CSS = """
 body{font-family:'SF Pro Display','Helvetica Neue',system-ui,sans-serif;margin:0;background:#F7F6F3;color:#2F3437}
 .wrap{max-width:1280px;margin:0 auto;padding:24px 20px}
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
 button{font:inherit;background:#0a7d33;color:#fff;border:0;border-radius:8px;padding:11px 20px;cursor:pointer;transition:background 150ms ease,transform 120ms ease-out}
 button:hover{background:#086b2b}
 button:active{transform:scale(.97)}
 .okmsg{color:#346538;font-weight:600;margin-left:10px}
 .hint{color:#787774;font-size:.86rem;line-height:1.5;margin-top:8px}
""" + _NAV_CSS


_TL_CSS = """
 .tl{background:#fff;border:1px solid #EAEAEA;border-radius:12px;padding:6px 16px;max-height:340px;overflow:auto;margin-bottom:22px}
 .tl-row{display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #F2F1EE;font-size:.9rem}
 .tl-row:last-child{border-bottom:0}
 .tl-when{color:#787774;font-variant-numeric:tabular-nums;white-space:nowrap;width:82px;flex:none;font-size:.82rem;padding-top:1px}
 .tl-dot{width:9px;height:9px;border-radius:50%;flex:none;margin-top:6px;background:#C9C7C2}
 .tl-dot.bill{background:#2F3437}.tl-dot.money{background:#0a7d33}.tl-dot.msg{background:#9fb8a4}
 .tl-dot.tap{background:#956400}.tl-dot.open{background:#e6d8a8}.tl-dot.promise{background:#1F6C9F}
 .tl-txt{flex:1;min-width:0}
 .tl-more{color:#787774;font-size:.8rem;padding:8px 0}
"""

_TODAY_CSS = """
 .hi{font-size:1.6rem;font-weight:700;letter-spacing:-.02em;margin:0}
 .day{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:22px;align-items:start}
 @media(max-width:900px){.day{grid-template-columns:1fr}}
 .side .hcard{margin-top:14px}
 .side .hcard .n{font-size:1.5rem}
 .side .sec{margin-top:22px}
 .xrow .note{color:#787774;font-size:.8rem;margin-left:6px}
 .xrow .ago{color:#787774;font-size:.78rem;white-space:nowrap}
 .xmore{color:#787774;font-size:.84rem;padding:10px 16px;border-top:1px solid #EAEAEA}
 .xmore a{color:#0a7d33;font-weight:600;text-decoration:none}
 .drow .cmain .mt{color:#2F3437}
 .kdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
 .kdot.setup{background:#9F2F2D}.kdot.promise_broken{background:#956400}.kdot.post_failed{background:#9F2F2D}.kdot.no_number{background:#787774}
 .sk i{display:block;height:54px;border-radius:12px;background:linear-gradient(90deg,#F2F1EE 25%,#FAFAF8 50%,#F2F1EE 75%);background-size:400% 100%;animation:skm 1.2s infinite;margin-bottom:10px}
 @keyframes skm{0%{background-position:100% 0}100%{background-position:0 0}}
 @media(prefers-reduced-motion:reduce){.sk i{animation:none}}
 .hd{color:#787774;font-size:.9rem;margin-top:2px}
 .synp{display:inline-block;font-size:.74rem;font-weight:600;padding:3px 10px;border-radius:9999px;margin-top:8px}
 .synp.ok{background:#EDF3EC;color:#346538}
 .synp.stale{background:#FBF3DB;color:#956400}
 .hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:12px 0 0}
 .glance{margin-top:26px}
 .hcard{background:#fff;border:1px solid #EAEAEA;border-radius:14px;padding:20px}
 .hcard .l{color:#787774;font-size:.74rem;text-transform:uppercase;letter-spacing:.05em}
 .hcard .n{font-size:2rem;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;margin-top:6px;line-height:1}
 .hcard .s{color:#787774;font-size:.82rem;margin-top:7px;line-height:1.4}
 .hcard.accent{background:#EDF3EC;border-color:#cfe4d0}
 .hcard .up{color:#346538;font-weight:600}
 .hcard .dn{color:#9F2F2D;font-weight:600}
 .sec{margin-top:26px}
 .sec h2{font-size:1.05rem;font-weight:700;margin:0 0 4px;letter-spacing:-.01em}
 .sec .sub{color:#787774;font-size:.84rem;margin-bottom:12px}
 .clist{background:#fff;border:1px solid #EAEAEA;border-radius:14px;overflow:hidden}
 .crow{display:flex;gap:12px;align-items:center;padding:13px 16px;border-bottom:1px solid #EAEAEA;flex-wrap:wrap}
 .crow:last-child{border-bottom:0}
 .crank{width:22px;height:22px;flex-shrink:0;border-radius:6px;background:#F7F6F3;color:#787774;font-size:.78rem;font-weight:700;display:flex;align-items:center;justify-content:center}
 .cmain{flex:1;min-width:150px}
 .cmain .nm{font-weight:600;font-size:.96rem}
 .cmain .mt{color:#787774;font-size:.8rem;margin-top:2px;font-variant-numeric:tabular-nums}
 .camt{font-weight:700;font-variant-numeric:tabular-nums;font-size:1rem;white-space:nowrap}
 .late{display:inline-block;font-size:.72rem;font-weight:700;padding:1px 7px;border-radius:9999px;background:#FDEBEC;color:#9F2F2D;margin-left:6px}
 .rel{display:inline-block;font-size:.7rem;font-weight:700;padding:1px 8px;border-radius:9999px;margin-left:6px;vertical-align:middle}
 .cacts{display:flex;gap:7px;flex-shrink:0}
 .cacts a,.cacts button{font-size:.8rem;padding:7px 12px;border-radius:7px;text-decoration:none;cursor:pointer}
 .cacts .rem{background:#0a7d33;color:#fff;border:0}
 .cacts .op{background:#fff;color:#2F3437;border:1px solid #EAEAEA}
 .cacts .op:hover{background:#f4f4f1}
 .empty{color:#787774;padding:22px 16px;text-align:center;line-height:1.5}
 .nudge{background:#FBF3DB;border:1px solid #f0e3b8;border-radius:12px;padding:16px 18px;margin-top:18px;color:#7a5c12;font-size:.9rem;line-height:1.5}
 .nudge a{color:#956400;font-weight:700}
 .flash{max-height:0;overflow:hidden;opacity:0;transition:all .25s ease;background:#EDF3EC;color:#346538;border-radius:10px;font-weight:600;padding:0 14px}
 .flash.on{max-height:60px;opacity:1;padding:11px 14px;margin-top:12px}
 .twocol{display:grid;grid-template-columns:1fr 1fr;gap:18px}
 @media(max-width:720px){.twocol{grid-template-columns:1fr}}
 .cacts .call{background:#EDF3EC;color:#346538;border:1px solid #cfe4d0;font-variant-numeric:tabular-nums}
 .cacts .call:hover{background:#e2eee2}
 .cacts .warn{background:#FBF3DB;color:#7a5c12;border:1px solid #f0e3b8}
 .nudgebtn{background:#956400;color:#fff;border:0;border-radius:8px;padding:8px 14px;font:inherit;font-weight:600;cursor:pointer;margin-left:4px}
 .nudgebtn:hover{background:#7a5203}
 .cfback{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:20;align-items:center;justify-content:center}
 .cfbox{background:#fff;border-radius:14px;padding:22px 24px;max-width:460px;width:92%;max-height:88vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,.25)}
 .cfbox h3{margin:0 0 6px;font-size:1.15rem}
 button.ghost{background:#fff;color:#2F3437;border:1px solid #EAEAEA;border-radius:8px;padding:9px 16px;font:inherit;cursor:pointer}
 button.ghost:hover{background:#f4f4f1}
 .anrow{display:flex;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid #EAEAEA;flex-wrap:wrap}
 .anrow:last-child{border-bottom:0}
 .anmain{flex:1;min-width:120px}
 .aninp{width:150px;font:inherit;padding:8px 10px;border:1px solid #EAEAEA;border-radius:7px;font-variant-numeric:tabular-nums}
 .saved{color:#346538;font-weight:700}
"""


@router.get("/admin/today", response_class=HTMLResponse)
def admin_today(token: str = Query(...), lang: str = Query("english")):
    """The Today home: the single reason to open ASVA each morning. Answers
    'did I get paid?' and 'who do I chase today?' in one glance, then hands the
    owner one-tap actions on the ranked chase list. All numbers are honest -
    built from the shop's own bills and booked receipts by app.services.today."""
    biz = _biz_by_token(token)
    en = _is_en(lang)
    title = biz.get("business_name") or "ASVA"
    warmup_banner = _warmup_banner(biz)
    # Shops whose WhatsApp still lives on this computer: one quiet line pointing
    # at the server move (reminders then go out with the laptop off).
    wa_banner = ("" if biz.get("wa_transport") == "server" else
                 f'<a class="flash" style="display:block;text-decoration:none" '
                 f'href="/admin/whatsapp?token={_h(token)}&lang={_h(lang)}">'
                 f'Keep reminders going when this computer is off: move WhatsApp to '
                 f'ASVA&#39;s server (2 minutes) &rarr;</a>')
    body = warmup_banner + wa_banner + """<div id="thead"><h1 class="hi">Loading...</h1></div>
<div id="tflash" class="flash"></div>
<div id="twrap"><div class="sk" style="margin-top:18px"><i></i><i></i><i></i></div></div>

<div id="addnum" class="cfback" style="display:none">
  <div class="cfbox">
    <h3>Add WhatsApp numbers</h3>
    <div class="hint" style="margin:2px 0 10px">These customers owe you but have no number saved. Add a 10-digit mobile to start reminding them. A number in Tally always wins later.</div>
    <div id="addnumlist"></div>
    <div style="margin-top:16px;display:flex;gap:10px">
      <button class="ghost" onclick="closeAddNum()">Done</button>
    </div>
  </div>
</div>
<script>
const TOKEN = __TOKEN__;

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

__I18N__
const EN = (LANG==='english');
// This same page opens inside the ASVA desktop app (no phone dialer) AND on a
// phone browser on the LAN. A tel: link is dead on a PC, so on desktop we offer
// Copy number + WhatsApp instead of Call.
const IS_DESKTOP = /Electron|ASVA-Desktop/i.test(navigator.userAgent || '');
function waDigits(p){ let d=String(p||'').replace(/\\D/g,''); if(d.length===10) d='91'+d; return d; }
let T = null;
function esc(s){ return String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }
function fmt(n){ try{ return '\\u20b9'+Number(Math.round(n||0)).toLocaleString('en-IN'); }catch(e){ return '\\u20b9'+n; } }
function short(n){ n=Number(n||0);
  if(n>=10000000) return '\\u20b9'+(n/10000000).toFixed(n>=1e8?0:1).replace(/\\.0$/,'')+' Cr';
  if(n>=100000) return '\\u20b9'+(n/100000).toFixed(n>=1e6?0:1).replace(/\\.0$/,'')+' L';
  if(n>=1000) return '\\u20b9'+(n/1000).toFixed(n>=10000?0:1).replace(/\\.0$/,'')+' K';
  return '\\u20b9'+Math.round(n).toLocaleString('en-IN'); }
function flash(m){ const f=document.getElementById('tflash'); if(!f)return; f.textContent=m; f.classList.add('on'); setTimeout(()=>f.classList.remove('on'),4000); }
let LOADED_ONCE = false;
async function load(){
  let data;
  try{
    const r = await fetch('/admin/today/data?token='+encodeURIComponent(TOKEN));
    if(!r.ok) throw new Error('http '+r.status);
    data = await r.json();
  }catch(e){
    // Never blank the page: if we already showed something, keep it and just
    // flash; only show the retry card on the very first failed load.
    if(!LOADED_ONCE)
      document.getElementById('twrap').innerHTML='<div class="hcard" style="margin-top:18px">'+TT('Could not load your day.')+' <a href="#" onclick="load();return false">'+TT('Retry')+'</a></div>';
    else flash(TT('Could not refresh just now. Showing your last view.'));
    return;
  }
  T = data;
  safeRender();
}
// A render error must NEVER leave a blank screen. Catch it, keep the last good
// view (or a retry card), and report - so a bad data shape can't white-screen.
function safeRender(){
  try{ render(); LOADED_ONCE = true; }
  catch(e){
    try{ console.error('today render failed', e); }catch(_ ){}
    if(!LOADED_ONCE)
      document.getElementById('twrap').innerHTML='<div class="hcard" style="margin-top:18px">'+TT('Something went wrong drawing this page.')+' <a href="#" onclick="load();return false">'+TT('Reload')+'</a></div>';
    else flash(TT('Display hiccup - press Refresh view if it looks off.'));
  }
}
function heroCard(label, big, sub, cls){
  return '<div class="hcard '+(cls||'')+'"><div class="l">'+esc(label)+'</div><div class="n">'+big+'</div>'+(sub?'<div class="s">'+sub+'</div>':'')+'</div>';
}
function render(){
  const t=T; if(!t) return;
  document.getElementById('thead').innerHTML =
    '<h1 class="hi">'+esc(t.greeting)+'</h1>'+
    '<div class="hd">'+esc(t.business)+' \\u00b7 '+esc(t.date_label)+'</div>'+
    (t.sync? '<span class="synp '+(t.sync.stale?'stale':'ok')+'">'+esc(t.sync.label)+'</span>' : '');

  // ── Hero: the morning brief ──────────────────────────────────────
  const mi=t.money_in||{}, y=mi.yesterday||{}, td=mi.today||{};
  const ci=t.coming_in||{}, rc=t.recovered||{}, os=t.outstanding||{}, ds=t.dso||{};
  let miSub = td.count>0 ? (TT('Today so far')+': '+fmt(td.amount)+' \\u00b7 '+td.count+' '+TT(td.count>1?'payments':'payment')) :
              (y.count>0 ? (y.count+' '+TT(y.count>1?'payments':'payment')+' '+TT('booked')) : TT('No payments booked yet'));
  let ciSub = [];
  if(ci.promised>0) ciSub.push(fmt(ci.promised)+' '+TT('promised'));
  if(ci.due_soon>0) ciSub.push(fmt(ci.due_soon)+' '+TT('due'));
  let rcSub = '';
  if(rc.last_month>0){
    rcSub = rc.up ? ('<span class="up">'+TT('Up')+' '+fmt(Math.abs(rc.delta))+'</span> '+TT('vs last month'))
                  : ('<span class="dn">'+TT('Down')+' '+fmt(Math.abs(rc.delta))+'</span> '+TT('vs last month'));
  } else { rcSub = 'in '+esc(rc.month_label||''); }
  let dsSub = os.party_count>0 ? (os.party_count+' '+TT('parties owe you')) : TT('across your open bills');

  let hero = '<div class="hero">'+
    heroCard(TT('Money in yesterday'), short(y.amount||0), miSub, '') +
    heroCard(TT('Coming in')+' ('+(ci.days||7)+' '+TT('days')+')', short(ci.total||0), (ciSub.length?ciSub.join(' + '):TT('Nothing promised yet')), '') +
    heroCard(TT('Recovered')+' '+esc(rc.month_label||TT('this month')), short(rc.this_month||0), rcSub, 'accent') +
    heroCard(TT('Money stuck'), (ds.days_stuck||0)+' '+TT('days'), dsSub, '') +
  '</div>';

  // ── Who to chase today (the daily action) ────────────────────────
  const C=t.chase||[];
  let chaseRows = C.length ? C.map((c,i)=>{
    const late = c.days_late>0 ? '<span class="late">'+c.days_late+TT('d late')+'</span>' : '';
    const grade = (c.grade && c.grade!=='new')
      ? ' <span class="rel" style="background:'+esc(c.grade_color||'#6b7770')+'22;color:'+esc(c.grade_color||'#6b7770')+'">'+esc(c.grade_label||'')+'</span>' : '';
    // The number is ALWAYS shown as plain text below the name, so the owner can
    // read it and dial on his phone on ANY device. The action button then differs:
    //  - on a phone, one tap Calls (tel:);
    //  - in the desktop app (a PC has no dialer, so tel: is dead), WhatsApp opens
    //    the chat with this customer - where he can message OR voice-call them.
    // Plus Remind (ASVA sends its reminder) on both. No number: one tap to add it.
    let acts;
    if(c.has_number){
      const contact = IS_DESKTOP
        ? '<a class="op call" target="_blank" rel="noopener" href="https://wa.me/'+esc(waDigits(c.phone))+'">WhatsApp</a>'
        : '<a class="op call" href="tel:'+esc(c.phone||'')+'">'+TT('Call')+'</a>';
      acts = contact +
             '<button class="rem" onclick=\\'remind('+JSON.stringify(c.name)+',this)\\'>'+TT('Remind')+'</button>';
    } else {
      acts = '<button class="op warn" onclick=\\'openAddNum('+JSON.stringify(c.client_id)+')\\'>'+TT('Add number')+'</button>';
    }
    const numline = (c.has_number && c.phone) ? (' \\u00b7 '+esc(c.phone)) : (c.has_number?'':' \\u00b7 '+TT('no WhatsApp number'));
    return '<div class="crow"><div class="crank">'+(i+1)+'</div>'+
      '<div class="cmain"><div class="nm">'+esc(c.display)+late+grade+'</div>'+
      '<div class="mt">'+(c.days_late>0?(c.days_late+' '+TT('days past due')):'')+numline+'</div></div>'+
      '<div class="camt">'+fmt(c.amount)+'</div>'+
      '<div class="cacts">'+acts+'</div></div>';
  }).join('') : '<div class="empty">'+TT('Nothing overdue right now. When bills cross their due date, the ones worth chasing show here, biggest first.')+'</div>';

  let chaseHead = C.length
    ? (TT('Top')+' '+C.length+' '+TT('by money at stake')+(t.chase_overdue_count>C.length?(' '+TT('of')+' '+t.chase_overdue_count+' '+TT('overdue')):''))
    : '';

  // ── Promises: who said they will pay ─────────────────────────────
  const PR=(t.promises&&t.promises.items)||[];
  let promRows = PR.length ? PR.map(p=>(
    '<div class="crow"><div class="cmain"><div class="nm">'+esc(p.display)+'</div>'+
    (p.said?'<div class="mt">\\u201c'+esc(p.said)+'\\u201d</div>':'')+
    (p.promise_date?'<div class="mt">'+TT('By')+' '+esc(p.promise_date)+'</div>':'')+'</div>'+
    '<div class="cacts"><a class="op" href="/admin/payments?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'">'+TT('Enter payment')+'</a></div></div>'
  )).join('') : '<div class="empty">'+TT('No open promises. When a customer says they paid or gives a date, it shows here.')+'</div>';

  // ── Aging bars ───────────────────────────────────────────────────
  const B=(ds.buckets)||[]; const maxB=Math.max(1,...B.map(x=>x.amount||0));
  let ageRows = B.filter(b=>b.amount>0).map(b=>(
    '<div class="age"><div class="lbl">'+esc(b.label)+'</div>'+
    '<div class="barwrap"><i style="width:'+Math.max(3,Math.round((b.amount/maxB)*100))+'%"></i></div>'+
    '<div class="amt">'+fmt(b.amount)+'</div></div>'
  )).join('') || '<div class="empty">'+TT('No open bills.')+'</div>';

  // ── No-number nudge: ONLY the parties who OWE and have no number (not
  // "every party with no number"). Opens an inline add-number panel - no leaving
  // this page.

  // ── Money expected: taps on the pay page. Small by design: only open
  // taps, one per customer, five at most, "opened but did not tap" as one
  // muted line. Intent, never "paid" - the owner checks the bank, then posts.
  const X=(t.expected&&t.expected.items)||[], XM=(t.expected&&t.expected.more)||0, XO=(t.expected&&t.expected.opened_only)||0;
  let expSec='';
  if(X.length||XO){
    const xRows = X.map(x=>(
      '<div class="crow xrow"><div class="cmain"><div class="nm">'+esc(x.display)+
        (x.note?'<span class="note">'+esc(x.note)+'</span>':'')+'</div>'+
        '<div class="mt">'+TT('tapped Pay')+' <span class="ago">'+ago(x.at)+'</span> \u00b7 '+TT('look for this amount in your bank or UPI app')+'</div></div>'+
      '<div class="camt">'+fmt(x.amount)+'</div>'+
      '<div class="cacts"><a class="rem" style="text-decoration:none" href="/admin/payments?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'&focus='+encodeURIComponent(x.id)+'">'+TT('Received')+'</a>'+
        '<button class="op" onclick=\\'dismissTap('+JSON.stringify(x.id)+',this)\\'>'+TT('Not received')+'</button></div></div>'
    )).join('');
    let foot='';
    if(XM) foot += '<div class="xmore">'+XM+' '+TT('more on')+' <a href="/admin/payments?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'">'+TT('Payments')+'</a></div>';
    if(XO) foot += '<div class="xmore">'+XO+' '+TT(XO>1?'others opened your payment link today but did not tap Pay yet.':'other opened your payment link today but did not tap Pay yet.')+'</div>';
    expSec = '<div class="sec"><h2>'+TT('Money expected')+'</h2><div class="sub">'+TT('They tapped Pay on your link. Check it came in, then post it to Tally.')+'</div><div class="clist">'+xRows+foot+'</div></div>';
  }

  // ── Needs a decision: only what the owner alone can settle ───────
  const D=t.decisions||[];
  const dRows = D.length ? D.map(d=>{
    let act='';
    if(d.action==='promise') act='<button class="rem" onclick=\\'remind('+JSON.stringify(d.display)+',this)\\'>'+TT('Remind')+'</button><a class="op" href="/admin/payments?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'">'+TT('Enter payment')+'</a>';
    else if(d.action==='payments') act='<a class="op" href="/admin/payments?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'">'+TT('Review')+'</a>';
    else if(d.action==='numbers') act='<button class="op warn" onclick="openAddNum()">'+TT('Add numbers')+'</button>';
    else if(d.action==='setup') act='<a class="op" href="/admin/setup?token='+encodeURIComponent(TOKEN)+'&lang='+LANG+'">'+TT('Open setup')+'</a>';
    return '<div class="crow drow"><span class="kdot '+esc(d.kind)+'"></span><div class="cmain"><div class="nm">'+esc(d.display)+'</div><div class="mt">'+esc(d.text)+'</div></div><div class="cacts">'+act+'</div></div>';
  }).join('') : '<div class="empty">'+TT('Nothing needs a decision today.')+'</div>';

  // Main column: the three lists in priority order. Sidebar: the glance,
  // promises and aging, so the owner runs the day from one screen without
  // scrolling past numbers to reach the action.
  document.getElementById('twrap').innerHTML =
    '<div class="day"><div class="main">'+
      expSec +
      '<div class="sec"><h2>'+TT('Who to chase today')+'</h2><div class="sub">'+esc(chaseHead)+'</div><div class="clist">'+chaseRows+'</div></div>'+
      '<div class="sec"><h2>'+TT('Needs a decision')+'</h2><div class="sub">'+TT('Only what you can settle. Everything else ASVA handles.')+'</div><div class="clist">'+dRows+'</div></div>'+
    '</div><div class="side">'+
      hero.replace('class="hero"','class="hero" style="grid-template-columns:1fr;gap:0"') +
      '<div class="sec"><h2>'+TT('Said they will pay')+'</h2><div class="clist">'+promRows+'</div></div>'+
      '<div class="sec"><h2>'+TT('Where your money is stuck')+'</h2><div class="card">'+ageRows+'</div></div>'+
    '</div></div>';
}
function ago(iso){
  if(!iso) return '';
  const m = Math.round((Date.now() - new Date(iso).getTime())/60000);
  if(m < 2) return TT('just now');
  if(m < 60) return m+' '+TT('min ago');
  const h = Math.round(m/60); if(h < 24) return h+' '+TT('hr ago');
  const d = Math.round(h/24); return d===1 ? TT('yesterday') : d+' '+TT('days ago');
}
async function dismissTap(id, btn){
  if(btn){ btn.disabled=true; }
  try{ await fetch('/admin/pay-signals/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TOKEN,id:id})}); }catch(e){}
  load();
}
async function remind(party, btn){
  if(btn){ btn.disabled=true; btn.textContent=TT('Sending...'); }
  try{
    const r=await fetch('/admin/send-now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TOKEN,party:party})});
    const d=await r.json().catch(()=>({}));
    if(d && d.wa_down){ flash(TT('Shop WhatsApp not connected. Open WhatsApp Setup and scan the QR.')); }
    else if(r.ok){ flash('\\u2713 '+TT('Reminder sent to')+' '+party+'.'); }
    else { flash(TT('Could not send. Try again.')); }
  }catch(e){ flash(TT('Could not send. Try again.')); }
  if(btn){ btn.disabled=false; btn.textContent=TT('Remind'); }
}
// ── Inline "add a number" (never leaves this page) ───────────────────
function openAddNum(focusId){
  const P=((T&&T.no_number&&T.no_number.parties)||[]);
  const box=document.getElementById('addnumlist');
  if(!P.length){ box.innerHTML='<div class="empty">'+TT('Everyone who owes you has a number. Nice.')+'</div>'; }
  else{
    box.innerHTML = P.map(p=>(
      '<div class="anrow" data-id="'+esc(p.client_id)+'">'+
        '<div class="anmain"><div class="nm">'+esc(p.display)+'</div><div class="mt">'+TT('owes')+' '+fmt(p.amount)+'</div></div>'+
        '<input class="aninp" inputmode="numeric" maxlength="10" placeholder="'+TT('10-digit mobile')+'" '+(p.client_id===focusId?'autofocus':'')+'>'+
        '<button class="rem" onclick=\\'saveNum('+JSON.stringify(p.client_id)+',this)\\'>'+TT('Save')+'</button>'+
      '</div>'
    )).join('');
  }
  document.getElementById('addnum').style.display='flex';
}
function closeAddNum(){ document.getElementById('addnum').style.display='none'; load(); }
async function saveNum(clientId, btn){
  const row=btn.closest('.anrow'); const inp=row.querySelector('.aninp');
  const num=(inp.value||'').replace(/\\D/g,'');
  if(num.length!==10 || !'6789'.includes(num[0])){ inp.style.borderColor='#c0392b'; inp.focus(); return; }
  btn.disabled=true; btn.textContent='...';
  try{
    const r=await fetch('/admin/set-number',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TOKEN,client_id:clientId,number:num})});
    if(r.ok){ row.innerHTML='<div class="anmain"><div class="nm">'+esc(row.querySelector('.nm')?row.querySelector('.nm').textContent:'')+'</div></div><span class="saved">\\u2713 '+TT('Saved')+'</span>';
      T.no_number.parties=(T.no_number.parties||[]).filter(x=>x.client_id!==clientId);
      T.no_number.count=Math.max(0,(T.no_number.count||1)-1); }
    else { btn.disabled=false; btn.textContent=TT('Save'); inp.style.borderColor='#c0392b'; }
  }catch(e){ btn.disabled=false; btn.textContent=TT('Save'); }
}
load();
setInterval(load, 30000);
</script>""".replace("__TOKEN__", json.dumps(token)).replace("__I18N__", i18n.js_runtime(lang))
    # The handful of English strings the page paints BEFORE its JS runs (loading
    # text) and the persistent add-number panel are localised on the server.
    for _full, _key in (
        (">Loading...</h1>", "Loading..."),
        ("Add WhatsApp numbers", "Add WhatsApp numbers"),
        ("These customers owe you but have no number saved. Add a 10-digit mobile to start reminding them. A number in Tally always wins later.",
         "These customers owe you but have no number saved. Add a 10-digit mobile to start reminding them. A number in Tally always wins later."),
        (">Done</button>", "Done"),
    ):
        _loc = i18n.s(lang, _key)
        body = body.replace(_full, _full.replace(_key, _loc) if _key in _full else _loc)
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>{title} - Today</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}{_TODAY_CSS}</style>'
        f'<div class="wrap">{_topnav(token, lang, "today")}{body}</div>',
        lang))


@router.get("/admin/today/data")
def admin_today_data(token: str = Query(...)):
    """JSON behind the Today home. Best-effort - always returns a usable shape."""
    biz = _biz_by_token(token)
    db = require_db()
    from app.services import today as today_svc

    data = today_svc.build_today(db, biz["id"], biz)

    # Sync freshness pill (reuses the dashboard's own reading).
    synced_dt = _last_synced_at(db, biz["id"])
    label, _color = _tally_status(synced_dt)
    stale = True
    try:
        if synced_dt is not None:
            stale = synced_dt.astimezone(_TODAY_IST).date() != _dt.datetime.now(_TODAY_IST).date()
    except Exception:
        stale = True
    data["sync"] = {"label": f"Tally: {label}", "stale": stale}
    return data


_TODAY_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


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


def _inr_digits(n) -> str:
    """Indian-grouped digits ONLY (28,29,452) - no currency prefix, for the many
    places that already print a Rs/&#8377; glyph. Python's own ',' format is
    WESTERN (2,829,280) and misreads at a glance to anyone counting in lakhs."""
    n = int(round(float(n or 0)))
    s = str(abs(n))
    if len(s) > 3:
        last3, rest, parts = s[-3:], s[:-3], []
        while len(rest) > 2:
            parts.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    return ("-" if n < 0 else "") + s


def _inr(n) -> str:
    return "Rs " + _inr_digits(n)


@router.get("/admin/setup", response_class=HTMLResponse)
def admin_setup(token: str = Query(...), lang: str = Query("english")):
    """First-run wizard for a new shop: UPI + language + a starter batch in one
    screen, then a pointer to scan WhatsApp. Reuses /admin/accounts/save and
    /admin/batches. Existing shops can ignore it."""
    from app.models import CATEGORY_CREDIT_DAYS, CATEGORY_LABELS
    biz = _biz_by_token(token)
    upi_cur = (biz.get("upi_vpa") or "").replace('"', "&quot;")
    # Every choice control on this page carries an EXPLICIT colour. The shared
    # _CSS sets button{color:#fff} for the green primary, and a choice button
    # that forgets to restate its own colour renders white on white, so the
    # owner cannot read what they are picking.
    cat_opts = "".join(
        f'<option value="{k}" style="color:#2F3437;background:#fff">{v} &middot; '
        f'{CATEGORY_CREDIT_DAYS.get(k, 30)} days</option>'
        for k, v in CATEGORY_LABELS.items())
    cat_days = json.dumps(CATEGORY_CREDIT_DAYS)
    body = f"""
<div style="font-weight:800;letter-spacing:.18em;font-size:.78rem;color:#0a7d33">ASVA</div>
<h1 style="margin:6px 0 4px">Let us set up {_h(names.clean_business(biz['business_name']))}</h1>
<div class="muted">Four answers, about a minute. You can change any of it later from the
Reminders tab, and nothing goes out to a customer until you have seen it.</div>

<div class="card" style="margin-top:20px;max-width:620px">
 <div class="sq"><span class="sn">1</span><div class="sb">
   <div class="st">What do you sell?</div>
   <div class="hint" style="margin:2px 0 8px">Used only for customers whose credit period is
   not set in Tally. Wherever Tally has one, Tally always wins.</div>
   <select id="cat">{cat_opts}</select>
   <div class="hint" style="margin-top:8px">A customer with no period in Tally will get
   <b id="cdshow">60</b> days to pay.</div>
 </div></div>

 <div class="sq"><span class="sn">2</span><div class="sb">
   <div class="st">Which UPI ID should customers pay into?</div>
   <div class="hint" style="margin:2px 0 8px">Every reminder carries this as a tappable pay
   link and a QR code. This is what turns a reminder into a payment.</div>
   <input id="upi" value="{upi_cur}" placeholder="yourshop@okhdfcbank">
 </div></div>

 <div class="sq"><span class="sn">3</span><div class="sb">
   <div class="st">What language should your customers get?</div>
   <div class="hint" style="margin:2px 0 8px">Your own screens stay in the language you picked
   at the top. This is only what the customer reads.</div>
   <div class="seg" id="lang">
     <button type="button" data-v="english" class="on">English</button>
     <button type="button" data-v="hinglish">Hinglish</button>
   </div>
 </div></div>

 <div class="sq"><span class="sn">4</span><div class="sb">
   <div class="st">How should the reminders sound?</div>
   <div class="hint" style="margin:2px 0 8px">Gentle sends 3 reminders across a bill, Standard
   5 and Firm 6. All three stay polite: ASVA never gets harsher on its own.</div>
   <div class="seg" id="tone">
     <button type="button" data-v="gentle">Gentle</button>
     <button type="button" data-v="standard" class="on">Standard</button>
     <button type="button" data-v="firm">Firm</button>
   </div>
 </div></div>

 <div style="margin-top:22px;padding-top:18px;border-top:1px solid #EAEAEA">
   <button onclick="finish()" style="font-size:1.05rem;padding:13px 26px">Save and continue</button>
   <span id="msg" class="okmsg"></span></div>
 <div class="hint" id="donehint" style="display:none;margin-top:14px">
   <b>One step left: connect WhatsApp.</b><br>
   ASVA holds the connection on its own server, so reminders keep going out
   when this computer is off, asleep or being updated.
   <div style="margin-top:10px">
     <b>Your first week:</b> ASVA warms your number up slowly so WhatsApp never
     blocks it. Day one reaches your 20 most overdue customers, and it builds up
     from there until your whole list is covered. Seeing only a few go out on the
     first day is ASVA working, not ASVA failing.
   </div>
   <div style="margin-top:12px">
     <a href="/admin/whatsapp?token={_h(token)}&amp;lang={_h(lang)}"
        style="display:inline-block;background:#0a7d33;color:#fff;text-decoration:none;
               padding:11px 18px;border-radius:8px;font-weight:600">Connect WhatsApp now</a>
   </div>
 </div>
</div>
<style>
 .sq{{display:flex;gap:14px;padding:18px 0;border-top:1px solid #F0F0EE}}
 .sq:first-child{{border-top:0;padding-top:4px}}
 .sb{{flex:1;min-width:0}}
 .st{{font-weight:700;font-size:1.02rem;color:#2F3437}}
 .sn{{flex:none;width:28px;height:28px;border-radius:999px;background:#eaf7ee;color:#0a5d28;
   font-weight:800;font-size:.9rem;display:flex;align-items:center;justify-content:center;margin-top:1px}}
 #cat{{font:inherit;padding:11px 12px;border:1px solid #EAEAEA;border-radius:8px;
   background:#fff;color:#2F3437;width:100%;max-width:360px;box-sizing:border-box}}
 #upi{{max-width:360px}}
 .seg{{display:inline-flex;border:1px solid #EAEAEA;border-radius:8px;overflow:hidden;flex-wrap:wrap}}
 /* Explicit colour on BOTH states. The shared button rule is white-on-green,
    and a choice button that inherits it is unreadable on this white card. */
 .seg button{{background:#fff;color:#2F3437;border:0;border-radius:0;padding:10px 18px;cursor:pointer}}
 .seg button + button{{border-left:1px solid #EAEAEA}}
 .seg button:hover{{background:#F4F9F4}}
 .seg button.on{{background:#0a7d33;color:#fff}}
</style>
<script>
const TOKEN = {token!r};
const CAT_DAYS = {cat_days};

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

let LANG = 'english', TONE = 'standard';
function showCd() {{
  const d = CAT_DAYS[document.getElementById('cat').value];
  document.getElementById('cdshow').textContent = d || 30;
}}
document.getElementById('cat').addEventListener('change', showCd);
showCd();
document.querySelectorAll('#lang button').forEach(b => b.onclick = () => {{
  LANG = b.dataset.v; document.querySelectorAll('#lang button').forEach(x => x.classList.toggle('on', x===b)); }});
document.querySelectorAll('#tone button').forEach(b => b.onclick = () => {{
  TONE = b.dataset.v; document.querySelectorAll('#tone button').forEach(x => x.classList.toggle('on', x===b)); }});
async function finish() {{
  const msg = document.getElementById('msg'); msg.textContent = 'Saving...';
  try {{
    await fetch('/admin/accounts/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, upi_vpa: document.getElementById('upi').value,
        category: document.getElementById('cat').value}})}});
    await fetch('/admin/batches', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: TOKEN, batches: [{{name:'Standard', style:TONE, lang:LANG, disc:0, upi:'', line:''}}]}})}});
    msg.textContent = 'Saved';
    const h = document.getElementById('donehint');
    h.style.display = 'block'; h.scrollIntoView({{behavior: 'smooth', block: 'center'}});
  }} catch (e) {{ msg.textContent = 'Save failed'; }}
}}
</script>"""
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>ASVA - Setup</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{body}</div>', lang))


# ── WhatsApp on ASVA's server ───────────────────────────────────────────────
# The owner moves the shop's WhatsApp off this computer: a process on the
# server (wa_service/manager.js) runs it, so reminders and bills go out and
# replies are read while the laptop is off. The page polls /status, shows the
# server's QR, and the first "connected" flips the shop to wa_transport=server
# (from then on the laptop is handed nothing - no double sends).
class TokenPayload(BaseModel):
    token: str


def _wa_biz(token: str) -> dict:
    biz = _biz_by_token(token)
    row = (require_db().table("businesses")
           .select("wa_transport, wa_port, wa_server_requested_at, wa_linked_at")
           .eq("id", biz["id"]).limit(1).execute()).data if not {
               "wa_transport", "wa_port"} <= set(biz) else [biz]
    return {**biz, **((row or [{}])[0] or {})}


@router.post("/admin/whatsapp/start")
def admin_whatsapp_start(payload: TokenPayload):
    biz = _biz_by_token(payload.token)
    port = wa_server.request_server(require_db(), biz["id"])
    return {"ok": True, "port": port}


@router.post("/admin/whatsapp/disconnect")
def admin_whatsapp_disconnect(payload: TokenPayload):
    """Owner's choice: take this shop's WhatsApp off the ASVA server.

    Sending goes back to the shop's own computer, so reminders then need ASVA
    open there. Nothing about the shop's data changes.
    """
    biz = _wa_biz(payload.token)
    ok = wa_server.release_server(require_db(), biz["id"], biz.get("wa_port"))
    return {"ok": True, "logged_out": ok}


@router.post("/admin/whatsapp/relink")
def admin_whatsapp_relink(payload: TokenPayload):
    """Throw away this shop's server session and show a fresh QR.

    The child binds 127.0.0.1 on the VPS, so the shop's app cannot reach it
    directly; the backend proxies. Used when the phone says "could not link
    device" and the only way out is a clean re-scan.
    """
    biz = _wa_biz(payload.token)
    port = biz.get("wa_port")
    if not port:
        return {"ok": False, "detail": "No server session to relink yet."}
    import httpx
    try:
        r = httpx.post(f"{wa_server.child_url(int(port))}/api/wa/relink",
                       headers=wa_server._headers(), timeout=20.0)
        ok = r.status_code == 200
    except Exception:
        log.warning("relink: child on port %s did not answer", port)
        ok = False
    # The old link is gone either way, so never leave the row claiming it is
    # connected - that would keep the server delivering into a dead session.
    # Transport stays as it is ON PURPOSE: flipping it to "laptop" here would
    # hand the queue to a shop computer that no longer runs wa_service at all,
    # and every send would fail until the re-scan. Left on "server", the queue
    # simply waits for the new session, which mark_connected re-confirms.
    require_db().table("businesses").update(
        {"wa_ready": False}).eq("id", biz["id"]).execute()
    return {"ok": ok}


@router.get("/admin/whatsapp/status")
def admin_whatsapp_status(token: str = Query(...)):
    biz = _wa_biz(token)
    if not biz.get("wa_server_requested_at") or not biz.get("wa_port"):
        return {"phase": "not_started", "transport": biz.get("wa_transport") or "laptop"}
    # Someone is on the QR screen: hold the pairing window open. An unlinked
    # session costs ~86 MB and is stopped a quarter of an hour after the last
    # poll, so this is what keeps it alive while the owner is actually looking.
    if not biz.get("wa_linked_at"):
        wa_server.touch_request(require_db(), biz["id"])
    st = wa_server.status_sync(biz["wa_port"])
    ph = wa_server.phase(st)
    if ph == "connected" and biz.get("wa_transport") != "server":
        wa_server.mark_connected(require_db(), biz["id"],
                                 already_linked=bool(biz.get("wa_linked_at")))
    return {"phase": ph, "qr": (st or {}).get("qr") if ph == "qr" else None,
            "transport": "server" if ph == "connected" else (biz.get("wa_transport") or "laptop")}


@router.get("/admin/whatsapp", response_class=HTMLResponse)
def admin_whatsapp(token: str = Query(...), lang: str = Query("english")):
    biz = _biz_by_token(token)
    tok = json.dumps(token)
    body = f"""
<h1>WhatsApp</h1>
<div class="muted">{_h(biz.get('business_name') or 'Your shop')}</div>
<div class="card" style="margin-top:20px;max-width:620px">
 <p style="margin-top:0"><b>Run your shop's WhatsApp on ASVA's server.</b> Reminders and bills keep
 going out even when this computer is off, and customer replies are read straight away.</p>
 <p class="hint">Your phone and number stay the same. ASVA appears in WhatsApp under
 <b>Linked devices</b>, and you can remove it there any time.</p>
 <div id="wa_state" style="margin-top:16px">Checking...</div>
</div>
<script>
const TOKEN = {tok};
function el(h){{ document.getElementById('wa_state').innerHTML = h; }}
async function start(){{
  el('Starting your WhatsApp on the server. This takes about a minute...');
  try {{ await fetch('/admin/whatsapp/start', {{method:'POST', headers:{{'Content-Type':'application/json'}},
         body: JSON.stringify({{token: TOKEN}})}}); }} catch(e) {{}}
  poll();
}}
async function disconnect(){{
  if (!confirm('Take your WhatsApp off the ASVA server?\\n\\nReminders and bills will then go out only '
             + 'while ASVA is open on this computer. Nothing else changes, and you can move it back any time.')) return;
  el('Taking your WhatsApp off the server...');
  try {{ await fetch('/admin/whatsapp/disconnect', {{method:'POST', headers:{{'Content-Type':'application/json'}},
         body: JSON.stringify({{token: TOKEN}})}}); }} catch(e) {{}}
  setTimeout(poll, 1500);
}}
async function poll(){{
  let s = null;
  try {{ s = await (await fetch('/admin/whatsapp/status?token=' + encodeURIComponent(TOKEN))).json(); }} catch(e) {{}}
  if (!s) {{ el('Could not reach ASVA. Check the internet and try again.'); return setTimeout(poll, 5000); }}
  if (s.phase === 'not_started') {{
    el('<button onclick="start()">Move WhatsApp to ASVA\\'s server</button>'
     + '<div class="hint" style="margin-top:10px">About 2 minutes. Keep your phone ready.</div>');
    return;
  }}
  if (s.phase === 'starting') {{ el('Starting your WhatsApp on the server. This takes about a minute...'); return setTimeout(poll, 3000); }}
  if (s.phase === 'qr') {{
    el('<p><b>Scan this with the shop phone:</b> WhatsApp, then the three dots, then '
     + '<b>Linked devices</b>, then <b>Link a device</b>.</p>'
     + '<img src="' + s.qr + '" width="280" height="280" alt="WhatsApp QR code">'
     + '<div class="hint">The code changes every few seconds. Scan the one on screen.</div>');
    return setTimeout(poll, 3000);
  }}
  el('<p><b>Connected.</b> Your reminders and bills now go from ASVA\\'s server, even when this computer is off.</p>'
   + '<div class="hint">If WhatsApp shows an older ASVA entry under Linked devices, you can log it out. '
   + 'ASVA now sends only from the server.</div>'
   + '<button id="wa_off" onclick="disconnect()" style="margin-top:16px;background:#fff;color:#9F2F2D;'
   + 'border:1px solid #9F2F2D">Take my WhatsApp off the server</button>'
   + '<div class="hint" style="margin-top:8px">Your number is unlinked from ASVA\\'s server and sending moves '
   + 'back to this computer, so ASVA must be open here for reminders to go out. Your customers, bills and '
   + 'history are not touched. You can move it back any time.</div>');
}}
poll();
</script>"""
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>ASVA - WhatsApp</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{body}</div>', lang))


@router.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics(token: str = Query(...), lang: str = Query("english")):
    biz = _biz_by_token(token)
    db = require_db()
    today = _today()

    names = {c["id"]: c["name"] for c in _fetch_paged(db, "clients", "id, name", biz["id"])}
    bills = _fetch_paged(db, "bills", "client_id, outstanding, due_date, invoice_date", biz["id"],
                         status_in=["pending", "partial", "overdue"])

    total = Decimal(0)
    by_client: dict = defaultdict(Decimal)
    overdue_parties: set = set()
    od_by_client: dict = defaultdict(int)
    age_wsum = Decimal(0)   # sum(outstanding * age_days) for a money-weighted DSO
    buckets = {"Not due": Decimal(0), "1-30": Decimal(0), "31-60": Decimal(0),
               "61-90": Decimal(0), "90+": Decimal(0)}
    for b in bills:
        out = Decimal(str(b.get("outstanding") or 0))
        if out <= 0:
            continue
        cid = b["client_id"]
        total += out
        by_client[cid] += out
        try:
            age = max((today - _dt.date.fromisoformat(str(b.get("invoice_date")))).days, 0)
            age_wsum += out * age
        except (TypeError, ValueError):
            pass
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

    # ── Collections from Tally receipts: this month + last-6-months trend ──
    receipts = []
    try:
        receipts = _fetch_paged(db, "tally_receipts", "amount, receipt_date", biz["id"])
    except Exception:
        receipts = []
    month_coll: dict = defaultdict(Decimal)
    for r in receipts:
        d = str(r.get("receipt_date") or "")[:7]
        if len(d) == 7:
            month_coll[d] += Decimal(str(r.get("amount") or 0))
    this_m = today.strftime("%Y-%m")
    coll_this = month_coll.get(this_m, Decimal(0))
    coll_count = sum(1 for r in receipts if str(r.get("receipt_date") or "")[:7] == this_m)
    # Money-weighted DSO: average days each rupee has been outstanding.
    dso = int(age_wsum / total) if total > 0 else 0
    # Collection rate this month = collected / (collected + still outstanding).
    denom = coll_this + total
    coll_rate = int(coll_this * 100 / denom) if denom > 0 else 0

    kpis = [
        (_inr(total), "Total baaki"),
        (_inr(coll_this), "Is month aaya"),
        (f"{coll_rate}%", "Collection rate"),
        (f"{dso} din", "Avg DSO"),
        (str(len(overdue_parties)), "Overdue parties"),
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

    # Last 6 months collections trend.
    last6 = []
    m0 = today.replace(day=1)
    for _ in range(6):
        last6.append((m0.strftime("%b"), month_coll.get(m0.strftime("%Y-%m"), Decimal(0))))
        m0 = (m0 - _dt.timedelta(days=1)).replace(day=1)
    last6.reverse()
    cmax = max((v for _, v in last6), default=Decimal(1)) or Decimal(1)
    coll_html = "".join(
        f'<div class="age"><div class="lbl">{m}</div>'
        f'<div class="barwrap"><i style="width:{float(v / cmax * 100):.1f}%;background:#346538"></i></div>'
        f'<div class="amt">{_inr(v)}</div></div>'
        for m, v in last6)

    top = sorted(by_client.items(), key=lambda kv: kv[1], reverse=True)[:12]
    rows_html = "".join(
        f'<tr><td>{names.get(cid, "?")}</td><td class="n">{_inr(amt)}</td>'
        f'<td class="n">{od_by_client.get(cid, 0)} din</td></tr>'
        for cid, amt in top)

    body = (
        f'<h1>Analytics</h1><div class="muted">{biz["business_name"]}</div>'
        f'<div class="grid">{kpi_html}</div>'
        f'<h2>Collections (last 6 months)</h2><div class="card">{coll_html}</div>'
        f'<h2>Aging (kitne din se baaki)</h2><div class="card">{age_html}</div>'
        f'<h2>Sabse zyada baaki (top 12)</h2>'
        f'<table><tr><th>Party</th><th class="n">Baaki</th><th class="n">Overdue</th></tr>{rows_html}</table>'
    )
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>{biz.get("business_name") or "ASVA"} - Analytics</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{_topnav(token, lang, "analytics")}{body}</div>',
        lang))


def _promise_head(pr: dict, name: str, en: bool) -> str:
    """One human line describing what the customer said, for the Payments tab."""
    kind = pr.get("kind")
    amt = f" {_inr(pr['amount'])}" if pr.get("amount") else ""
    if kind == "paid_claim":
        return (f"{name} says they have already paid{amt}"
                if en else f"{name} ne kaha already pay kar diya{amt}")
    if kind == "promise" and pr.get("promise_date"):
        return (f"{name} promised to pay by {str(pr['promise_date'])[:10]}{amt}"
                if en else f"{name} ne {str(pr['promise_date'])[:10]} tak dene ka kaha{amt}")
    return (f"{name} - payment update{amt}" if en else f"{name} - payment update{amt}")


@router.get("/admin/payments", response_class=HTMLResponse)
def admin_payments(token: str = Query(...), lang: str = Query("english")):
    """The Payments tab: AI-read promise/claim ledger (B6) on the left, and the
    receipt queue awaiting confirm-and-post-to-Tally (C7) on the right."""
    biz = _biz_by_token(token)
    en = _is_en(lang)
    title = biz.get("business_name") or "ASVA"
    body = f"""<h1>Payments</h1><div class="muted">{title}</div>
<div class="hint" style="margin:6px 0 2px">When a customer says they paid, it appears here. Check the amount and the deposit account, then post it into Tally with one tap. ASVA never posts on its own.</div>
<div style="margin:12px 0 2px"><button class="mini" onclick="openAdd()">+ Add a payment</button></div>
<div id="pmflash" class="flash"></div>
<div id="pmwrap"><div class="sk" style="margin-top:18px"><i></i><i></i><i></i></div></div>

<div id="cf" class="cfback" style="display:none">
  <div class="cfbox">
    <h3 id="cftitle">Enter payment</h3>
    <div class="cfparty" id="cfparty"></div>
    <div class="hint" id="cfocr" style="margin:2px 0 8px"></div>
    <label>Amount received</label>
    <input id="cfamt" type="number" min="1" step="0.01" inputmode="decimal">
    <label>Deposit into (Tally account)</label>
    <select id="cfdep"></select>
    <label>Date of entry</label>
    <input id="cfdate" type="date">
    <div class="hint" id="cfalloc" style="margin-top:10px"></div>
    <div style="margin-top:18px;display:flex;gap:10px">
      <button onclick="postReceipt()" id="cfpost">Post to Tally</button>
      <button class="ghost" onclick="closeCf()">Cancel</button>
    </div>
    <div id="cfmsg" class="okmsg" style="display:block;margin:10px 0 0"></div>
  </div>
</div>

<div id="addcf" class="cfback" style="display:none">
  <div class="cfbox">
    <h3>Add a payment</h3>
    <div class="hint" style="margin:2px 0 8px">Type the customer's name to find them. You confirm the amount and deposit account next, then post it into Tally. Nothing posts without your confirm.</div>
    <label>Customer</label>
    <input id="addsearch" placeholder="Type a name to search..." autocomplete="off" oninput="filterAdd()"
      style="width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #EAEAEA;border-radius:8px;font:inherit">
    <div id="addlist" style="max-height:240px;overflow:auto;border:1px solid #EAEAEA;border-radius:8px;margin-top:8px;display:none"></div>
    <div style="margin-top:18px;display:flex;gap:10px">
      <button onclick="pickAdd()" id="addnext">Next</button>
      <button class="ghost" onclick="closeAdd()">Cancel</button>
    </div>
    <div id="addmsg" class="okmsg" style="display:block;margin:10px 0 0"></div>
  </div>
</div>
<script>
const TOKEN = {token!r};

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

const EN = {str(en).lower()};
let DATA = {{promises:[], pending:[], deposit_ledgers:[]}};
let CF = null;   // the receipt being confirmed
function esc(s){{return String(s==null?'':s).replace(/[&<>]/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[m]));}}
function inr(n){{try{{return Number(n).toLocaleString('en-IN');}}catch(e){{return n;}}}}
let PREV_INFLIGHT = null;   // ids that were pending/confirmed last load
function flash(msg){{
  const f=document.getElementById('pmflash'); if(!f)return;
  f.textContent=msg; f.classList.add('on');
  setTimeout(()=>{{f.classList.remove('on');}}, 4000);
}}
async function load(){{
  // Never refresh while the owner is mid-entry - re-rendering behind an open
  // popup closes a dropdown or loses the party they just picked. This covers BOTH
  // the confirm popup (#cf) and the add-payment search (#addcf), so a pause of
  // any length while entering a payment never wipes their selection.
  const cfOpen = document.getElementById('cf').style.display==='flex';
  const addOpen = (document.getElementById('addcf')||{{}}).style && document.getElementById('addcf').style.display==='flex';
  if(cfOpen || addOpen) return;
  let prev = PREV_INFLIGHT;
  try{{
    const r = await fetch('/admin/payments/data?token='+encodeURIComponent(TOKEN));
    if(!r.ok) throw new Error('http '+r.status);
    DATA = await r.json();
  }}catch(e){{
    document.getElementById('pmwrap').innerHTML='<div class="card" style="margin-top:18px">Could not load. <a href="#" onclick="load();return false">Retry</a></div>';
    return;
  }}
  // A receipt that was in-flight last time and is gone now (not failed) reached
  // Tally: give the owner a clear, positive confirmation.
  const now = new Set((DATA.pending||[]).map(q=>q.id));
  const failedNow = new Set((DATA.pending||[]).filter(q=>q.status==='failed').map(q=>q.id));
  if(prev){{
    let posted=0;
    prev.forEach(id=>{{ if(!now.has(id) && !failedNow.has(id)) posted++; }});
    if(posted>0) flash('\\u2713 '+posted+' payment'+(posted>1?'s':'')+' posted to Tally.');
  }}
  PREV_INFLIGHT = new Set((DATA.pending||[]).filter(q=>q.status==='confirmed'||q.status==='posting').map(q=>q.id));
  render();
  focusTap();
}}
function statusChip(s){{
  if(s==='confirmed') return '<span class="chip wait">Waiting to post</span>';
  if(s==='posting')   return '<span class="chip wait">Posting to Tally...</span>';
  if(s==='failed')    return '<span class="chip bad">Failed</span>';
  return '';
}}
function render(){{
  const P = DATA.promises||[], Q = DATA.pending||[];
  let ph = P.length ? P.map(p=>(
    '<div class="prow"><div class="pmain"><b>'+esc(p.head)+'</b>'+
      (p.said?'<div class="said">&ldquo;'+esc(p.said)+'&rdquo;</div>':'')+
      '<div class="meta">'+esc(p.outstanding_line||'')+'</div></div>'+
    '<div class="pacts">'+
      '<button class="mini" onclick=\\'enter('+JSON.stringify(p.client_id)+')\\'>Enter payment</button>'+
      '<button class="mini ghost" onclick=\\'chase('+JSON.stringify(p.client_id)+')\\'>Chase</button>'+
    '</div></div>'
  )).join('') : '<div class="empty">No open promises. When a customer says they paid or gives a date, it shows here.</div>';

  let qh = Q.length ? Q.map(q=>(
    '<div class="prow"><div class="pmain"><b>'+esc(q.party_display||q.party_ledger)+'</b> '+statusChip(q.status)+
      '<div class="meta">&#8377;'+inr(q.amount)+' &middot; into '+esc(q.deposit_ledger||'Cash')+'</div>'+
      (q.error?'<div class="said" style="color:#9f2f2d">'+esc(q.error)+'</div>':'')+
      (q.agent_offline?'<div class="said" style="color:#956400">&#9888; Waiting '+q.waiting_mins+'m &middot; open ASVA on the shop PC (Tally open) so this posts. It will send itself once the agent runs.</div>':'')+
    '</div><div class="pacts">'+
      (q.status==='pending'||q.status==='failed'
        ? '<button class="mini" onclick=\\'enterPending('+JSON.stringify(q.id)+')\\'>Review &amp; post</button>'+
          '<button class="mini ghost" onclick=\\'cancelPending('+JSON.stringify(q.id)+')\\'>Remove</button>'
        : '<button class="mini ghost" onclick=\\'cancelPending('+JSON.stringify(q.id)+')\\'>Remove</button>')+
    '</div></div>'
  )).join('') : '<div class="empty">Nothing waiting to post. Reply PAID &lt;name&gt; on WhatsApp, or press Enter payment on a promise.</div>';

  const posting = Q.filter(q=>q.status==='confirmed'||q.status==='posting').length;
  const cP = P.length?(' <span class="cnt">'+P.length+'</span>'):'';
  const cQ = Q.length?(' <span class="cnt">'+Q.length+'</span>'):'';
  const postingNote = posting?('<div class="postnote">&#8635; Posting '+posting+' receipt'+(posting>1?'s':'')+' to Tally...</div>'):'';
  // Expected: customers who tapped Pay on the pay page. Intent, not money -
  // every word says "tapped", and the only way into Tally is the owner's
  // button. Resolved rows (Tally receipt / screenshot / owner) show a tick for
  // a day so the owner sees the loop close, then leave.
  const X = DATA.expected||[];
  let xh = '';
  if(X.length){{
    xh = '<h2>Expected <span class="cnt">'+X.filter(x=>!x.resolved_by).length+'</span></h2>'+
      '<div class="hint" style="margin:-4px 0 8px">They opened your payment link and tapped Pay. Check your bank or UPI app for exactly this amount, then post it. If it never came, press Not received.</div>'+
      '<div class="card pad0">'+X.map(x=>{{
        const when = ago(x.at);
        const what = '<b>'+esc(x.name)+'</b> tapped Pay <b>&#8377;'+inr(x.amount)+'</b>'+(x.note?' <span class="meta">('+esc(x.note)+')</span>':'');
        if(x.resolved_by){{
          const how = x.resolved_by==='tally' ? 'Received in Tally' : x.resolved_by==='screenshot' ? 'Customer sent proof' : 'You recorded it';
          return '<div class="prow"><div class="pmain">'+what+'<div class="meta">'+when+' &middot; <span style="color:#0a7d33">&#10003; '+how+'</span></div></div></div>';
        }}
        return '<div class="prow"><div class="pmain">'+what+'<div class="meta">'+when+' &middot; look for this amount in your bank or UPI app</div></div>'+
          '<div class="pacts">'+
            '<button class="mini" onclick=\\'enterTap('+JSON.stringify(x.id)+')\\'>Received, post to Tally</button>'+
            '<button class="mini ghost" onclick=\\'dismissTap('+JSON.stringify(x.id)+')\\'>Not received</button>'+
          '</div></div>';
      }}).join('')+'</div>';
  }}
  document.getElementById('pmwrap').innerHTML = xh +
    '<div class="cols">'+
      '<div class="colp"><h2>Customers who say they paid'+cP+'</h2><div class="card pad0">'+ph+'</div></div>'+
      '<div class="colp"><h2>Ready to post to Tally'+cQ+'</h2>'+postingNote+'<div class="card pad0">'+qh+'</div></div>'+
    '</div>';
}}
function depOpts(sel){{
  const L = DATA.deposit_ledgers||['Cash'];
  return L.map(x=>'<option value="'+esc(x)+'"'+(x===sel?' selected':'')+'>'+esc(x)+'</option>').join('');
}}
function today(){{ return new Date().toISOString().slice(0,10); }}
function openCf(title, party, amount, dep, dateStr, alloc){{
  document.getElementById('cftitle').textContent = title;
  document.getElementById('cfparty').textContent = party ? ((EN?'Party: ':'Party: ')+party) : '';
  document.getElementById('cfamt').value = amount||'';
  document.getElementById('cfdep').innerHTML = depOpts(dep||'Cash');
  document.getElementById('cfdate').value = dateStr || today();
  document.getElementById('cfalloc').textContent = alloc||'';
  document.getElementById('cfocr').innerHTML = '';
  document.getElementById('cfmsg').textContent = '';
  document.getElementById('cf').style.display='flex';
}}
function closeCf(){{document.getElementById('cf').style.display='none';CF=null;}}
function enter(clientId){{
  const p=(DATA.promises||[]).find(x=>x.client_id===clientId); if(!p)return;
  CF = {{mode:'promise', client_id:clientId, name:p.name}};
  openCf((EN?'Enter payment':'Payment'), p.name, p.outstanding||'', 'Cash', today(),
    (p.outstanding>0?((EN?'Full outstanding: ':'Poora baaki: ')+'\\u20b9'+inr(p.outstanding)):''));
}}
function enterPending(id){{
  const q=(DATA.pending||[]).find(x=>x.id===id); if(!q)return;
  CF = {{mode:'pending', id:id, name:q.party_display}};
  openCf((EN?'Review payment':'Payment'), q.party_display||q.party_ledger, q.amount,
    q.deposit_ledger||'Cash', q.receipt_date||today(), '');
  // Screenshot hints (suggestions only - you still edit + post).
  let bits=[];
  if(q.ocr_payer) bits.push('From: '+q.ocr_payer);
  if(q.ocr_ref) bits.push('Ref: '+q.ocr_ref);
  let html = bits.join(' &middot; ');
  if(q.ocr_confidence!=null && q.ocr_confidence < 0.6)
    html += (html?'<br>':'')+'<span style="color:#956400">&#9888; Blurry screenshot - please check the amount.</span>';
  document.getElementById('cfocr').innerHTML = html;
}}
async function postReceipt(){{
  if(!CF)return;
  const amt = parseFloat(document.getElementById('cfamt').value);
  const dep = document.getElementById('cfdep').value;
  const dt = document.getElementById('cfdate').value || today();
  if(!(amt>0)){{document.getElementById('cfmsg').textContent='Enter a valid amount.';return;}}
  document.getElementById('cfpost').disabled=true;
  document.getElementById('cfmsg').textContent='Sending to Tally...';
  let ok=false, detail='';
  try{{
    const url = (CF.mode==='promise') ? '/admin/promises/action' : '/admin/pending/confirm';
    const bodyObj = (CF.mode==='promise')
      ? {{token:TOKEN,client_id:CF.client_id,action:'enqueue',amount:amt,deposit_ledger:dep,receipt_date:dt}}
      : {{token:TOKEN,id:CF.id,amount:amt,deposit_ledger:dep,receipt_date:dt}};
    const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(bodyObj)}});
    ok=r.ok;
    if(!ok){{ const d=await r.json().catch(()=>({{}})); detail=(d&&d.detail)||''; }}
  }}catch(e){{ ok=false; detail='No connection to ASVA. Check the shop PC is on, then try again.'; }}
  document.getElementById('cfpost').disabled=false;
  // The payment is SAVED the moment this returns ok - it then posts to Tally when
  // the agent next runs (Tally open). Close and let the list show its status.
  if(ok){{ closeCf(); flash('\\u2713 Payment saved. It will post to Tally shortly.'); load(); }}
  else document.getElementById('cfmsg').textContent = detail || 'Could not save. Try again.';
}}
function ago(iso){{
  if(!iso) return '';
  const m = Math.round((Date.now() - new Date(iso).getTime())/60000);
  if(m < 2) return 'just now';
  if(m < 60) return m+' min ago';
  const h = Math.round(m/60); if(h < 24) return h+' hr ago';
  const d = Math.round(h/24); return d===1 ? 'yesterday' : d+' days ago';
}}
// Arriving from Today's "Received" button: open the pre-filled form at once.
const FOCUS = new URLSearchParams(location.search).get('focus') || '';
let FOCUSED = false;
function focusTap(){{
  if(!FOCUS || FOCUSED) return;
  if((DATA.expected||[]).some(e=>e.id===FOCUS)){{ FOCUSED = true; enterTap(FOCUS); }}
}}
function enterTap(id){{
  const x=(DATA.expected||[]).find(e=>e.id===id); if(!x)return;
  CF = {{mode:'promise', client_id:x.client_id, name:x.name}};
  openCf('Payment received', x.name, x.amount, 'Cash', today(),
    'Tapped Pay '+ago(x.at)+(x.note?' for '+x.note:'')+'. Check the amount against your bank or UPI app before posting.');
}}
async function dismissTap(id){{
  await fetch('/admin/pay-signals/dismiss',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{token:TOKEN,id:id}})}});
  load();
}}
async function chase(clientId){{
  await fetch('/admin/promises/action',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{token:TOKEN,client_id:clientId,action:'chase'}})}});
  load();
}}
async function cancelPending(id){{
  await fetch('/admin/pending/cancel',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{token:TOKEN,id:id}})}});
  load();
}}
let ADDCLIENTS=[];
let ADD_CID='';
function renderAddList(q){{
  const box=document.getElementById('addlist');
  q=(q||'').trim().toLowerCase();
  if(!ADDCLIENTS.length){{ box.style.display='block'; box.innerHTML='<div class="addrow" style="padding:12px;color:#787774">No customers found</div>'; return; }}
  let list = q ? ADDCLIENTS.filter(c=>(c.name||'').toLowerCase().includes(q)) : ADDCLIENTS;
  list = list.slice(0,60);
  box.style.display='block';
  box.innerHTML = list.length
    ? list.map(c=>'<div class="addrow" data-cid="'+esc(c.client_id)+'" onclick="pickRow(this.dataset.cid)"'
        +' style="padding:11px 13px;cursor:pointer;border-bottom:1px solid #F2F1EE;display:flex;justify-content:space-between;gap:10px">'
        +'<span>'+esc(c.name)+'</span>'+(c.outstanding>0?('<span style="color:#787774">\\u20b9'+inr(c.outstanding)+'</span>'):'')+'</div>').join('')
    : '<div class="addrow" style="padding:12px;color:#787774">No match. Try another spelling.</div>';
}}
function filterAdd(){{ ADD_CID=''; renderAddList(document.getElementById('addsearch').value); }}
function pickRow(cid){{
  ADD_CID=cid;
  const c=ADDCLIENTS.find(x=>x.client_id===cid)||{{}};
  document.getElementById('addsearch').value=c.name||'';
  const box=document.getElementById('addlist');
  box.innerHTML='<div class="addrow" style="padding:11px 13px;background:#EDF3EC;color:#346538;font-weight:600;display:flex;justify-content:space-between;gap:10px">'
    +'<span>\\u2713 '+esc(c.name||'')+'</span>'+(c.outstanding>0?('<span>\\u20b9'+inr(c.outstanding)+'</span>'):'')+'</div>';
}}
async function openAdd(){{
  document.getElementById('addmsg').textContent='';
  document.getElementById('addcf').style.display='flex';
  ADD_CID='';
  const si=document.getElementById('addsearch'); si.value='';
  const box=document.getElementById('addlist'); box.style.display='none'; box.innerHTML='';
  try{{
    const r=await fetch('/admin/payments/clients?token='+encodeURIComponent(TOKEN));
    const d=await r.json();
    ADDCLIENTS=d.clients||[];
    renderAddList('');
    si.focus();
  }}catch(e){{ box.style.display='block'; box.innerHTML='<div class="addrow" style="padding:12px;color:#9f2f2d">Could not load customers</div>'; }}
}}
function closeAdd(){{document.getElementById('addcf').style.display='none';}}
function pickAdd(){{
  const cid=ADD_CID;
  if(!cid){{document.getElementById('addmsg').textContent=EN?'Type a name and pick the customer.':'Naam type karke customer chunein.';return;}}
  const c=ADDCLIENTS.find(x=>x.client_id===cid)||{{}};
  CF={{mode:'promise', client_id:cid, name:c.name||''}};
  closeAdd();
  openCf((EN?'Enter payment':'Payment'), c.name||'', c.outstanding||'', 'Cash', today(),
    (c.outstanding>0?((EN?'Full outstanding: ':'Poora baaki: ')+'\\u20b9'+inr(c.outstanding)):''));
}}
load();
setInterval(load, 15000);   // reflect posted/failed status as the agent works
</script>"""
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>{title} - Payments</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}{_PAYMENTS_CSS}</style>'
        f'<div class="wrap">{_topnav(token, lang, "payments")}{body}</div>',
        lang))


_PAYMENTS_CSS = """
 .cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;margin-top:14px}
 .colp{min-width:0}
 .card.pad0{padding:0}
 .prow{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;padding:14px 16px;border-bottom:1px solid #EAEAEA}
 .prow:last-child{border-bottom:0}
 .pmain{flex:1;min-width:180px}
 .cnt{display:inline-block;background:#EDF3EC;color:#346538;font-size:.72rem;font-weight:700;border-radius:9999px;padding:1px 9px;vertical-align:middle;margin-left:4px}
 .postnote{color:#1F6C9F;font-size:.85rem;margin:2px 0 8px}
 .flash{max-height:0;overflow:hidden;opacity:0;transition:all .25s ease;background:#EDF3EC;color:#346538;border-radius:10px;font-weight:600;padding:0 14px}
 .flash.on{max-height:60px;opacity:1;padding:11px 14px;margin-top:12px}
 .pmain b{font-weight:600}
 .said{color:#5a6b60;font-size:.86rem;margin-top:4px;line-height:1.45}
 .meta{color:#787774;font-size:.82rem;margin-top:4px;font-variant-numeric:tabular-nums}
 .pacts{display:flex;flex-direction:column;gap:6px;flex-shrink:0}
 .mini{padding:7px 12px;font-size:.82rem;border-radius:7px}
 button.ghost{background:#fff;color:#2F3437;border:1px solid #EAEAEA}
 button.ghost:hover{background:#f4f4f1}
 .empty{color:#787774;padding:24px 16px;text-align:center;line-height:1.5}
 .chip{display:inline-block;font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:9999px;letter-spacing:.03em;vertical-align:middle}
 .chip.wait{background:#E1F3FE;color:#1F6C9F}
 .chip.bad{background:#FDEBEC;color:#9F2F2D}
 .cfback{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:20;align-items:center;justify-content:center}
 .cfbox{background:#fff;border-radius:14px;padding:22px 24px;max-width:420px;width:92%;max-height:90vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,.25)}
 .cfbox h3{margin:0 0 6px;font-size:1.1rem}
 .cfbox select,.cfbox input[type=date]{font:inherit;padding:10px 12px;border:1px solid #EAEAEA;border-radius:8px;background:#fff;width:100%;box-sizing:border-box}
 .cfparty{background:#F7F6F3;border:1px solid #EAEAEA;border-radius:8px;padding:8px 12px;font-weight:600;font-size:.92rem;margin-bottom:6px}
 .cfparty:empty{display:none}
"""


@router.get("/admin/payments/data")
def admin_payments_data(token: str = Query(...)):
    """JSON feed for the Payments tab: open promises (AI-read), the receipt queue,
    and the shop's deposit accounts."""
    from app.services import promises as promises_svc, receipts_queue as rq
    biz = _biz_by_token(token)
    db = require_db()
    bid = biz["id"]
    en = _is_en(biz.get("owner_language") or "english")

    prs = promises_svc.open_for_business(db, bid)
    # Names + outstanding for each promised party.
    cids = list({p["client_id"] for p in prs})
    cname = {}
    if cids:
        cr = (db.table("clients").select("id, name").in_("id", cids).execute()).data or []
        cname = {c["id"]: c.get("name") for c in cr}
    owed: dict = {}
    if cids:
        ob = (db.table("bills").select("client_id, outstanding")
              .eq("business_id", bid).in_("client_id", cids)
              .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
        for b in ob:
            owed[b["client_id"]] = owed.get(b["client_id"], Decimal(0)) + Decimal(str(b.get("outstanding") or 0))

    # Build both lists defensively: one malformed row (a null amount, a missing
    # ledger from an odd import, a duplicate-number party) must NEVER 500 the
    # whole tab - the owner would see "Could not load" with no way forward. Skip
    # the bad row, keep the rest.
    promises_out = []
    for p in prs:
        try:
            cid = p.get("client_id")
            if not cid:
                continue
            nm = names.clean_display(cname.get(cid) or "") or "A customer"
            out = owed.get(cid, Decimal(0))
            promises_out.append({
                "client_id": cid,
                "name": nm,
                "head": _promise_head(p, nm, en),
                "said": p.get("raw_text") or "",
                "outstanding": float(out),
                "outstanding_line": (f"Outstanding {_inr(out)}" if en else f"Baaki {_inr(out)}") if out > 0 else "",
            })
        except Exception:
            log.warning("skipping a malformed promise row (business %s)", bid, exc_info=True)

    _now = _dt.datetime.now(_dt.timezone.utc)
    def _mins_since(ts):
        try:
            t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.timezone.utc)
            return int((_now - t).total_seconds() // 60)
        except Exception:
            return None

    pending_out = []
    for r in rq.list_for_owner(db, bid):
        try:
            led = r.get("party_ledger") or r.get("party_display") or "Customer"
            st = r.get("status")
            # How long the owner has been waiting for this to reach Tally. If it
            # has been in confirm/post for a while, the shop's Tally agent is
            # almost certainly not running - tell the owner exactly that instead
            # of an endless "Posting...".
            wm = _mins_since(r.get("confirmed_at") or r.get("created_at"))
            pending_out.append({
                "id": r.get("id"), "party_ledger": led,
                "party_display": r.get("party_display") or led,
                "amount": float(r.get("amount") or 0),
                "deposit_ledger": r.get("deposit_ledger") or "Cash", "status": st,
                "receipt_date": str(r.get("receipt_date") or "")[:10], "error": r.get("error"),
                "waiting_mins": wm,
                "agent_offline": bool(wm is not None and wm >= 15 and st in ("confirmed", "posting")),
                "ocr_payer": r.get("ocr_payer"),
                "ocr_ref": r.get("ocr_ref"),
                "ocr_confidence": r.get("ocr_confidence"),
            })
        except Exception:
            log.warning("skipping a malformed receipt row (business %s)", bid, exc_info=True)

    # Expected payments: customers who tapped Pay on the pay page and whose
    # money has not yet been seen (Tally receipt, screenshot, or the owner's
    # own entry). INTENT, never a confirmed payment, so the wording downstream
    # says "tapped Pay", not "paid". Still the earliest signal the owner gets.
    from app.services import pay_signals as _ps
    expected = []
    try:
        taps = _ps.open_taps(db, bid)
        done = _ps.recently_resolved(db, bid)
        cids = list({e["client_id"] for e in taps + done if e.get("client_id")})
        ev_names = {}
        if cids:
            cr2 = (db.table("clients").select("id, name")
                   .in_("id", cids).execute()).data or []
            ev_names = {c["id"]: c.get("name") for c in cr2}
        for e in taps:
            expected.append({
                "id": e["id"], "client_id": e.get("client_id"),
                "name": names.clean_display(ev_names.get(e.get("client_id")) or "") or "A customer",
                "amount": float(e.get("amount") or 0), "note": e.get("note") or "",
                "at": e.get("created_at"), "resolved_by": None,
            })
        for e in done:
            expected.append({
                "id": e["id"], "client_id": e.get("client_id"),
                "name": names.clean_display(ev_names.get(e.get("client_id")) or "") or "A customer",
                "amount": float(e.get("amount") or 0), "note": e.get("note") or "",
                "at": e.get("created_at"), "resolved_by": e.get("resolved_by"),
                "resolved_at": e.get("resolved_at"),
            })
    except Exception:
        log.debug("expected payments unavailable", exc_info=True)
    # Kept for the dashboard row, which only wants the open ones.
    pay_signals = [x for x in expected if not x["resolved_by"]]

    return {
        "promises": promises_out,
        "pending": pending_out,
        "pay_signals": pay_signals,
        "expected": expected,
        "deposit_ledgers": rq.get_deposit_ledgers(db, bid),
    }


@router.get("/admin/payments/clients")
def admin_payments_clients(token: str = Query(...)):
    """Party picker for the Payments tab's 'Add a payment' button: the shop's
    customers with their current outstanding, most-owed first. Fetched lazily
    when the owner opens the add popup, so the 15s Payments poll stays light."""
    from app.services import names
    biz = _biz_by_token(token)
    db = require_db()
    bid = biz["id"]
    cl = (db.table("clients").select("id, name")
          .eq("business_id", bid).limit(3000).execute()).data or []
    owed: dict = {}
    ob = (db.table("bills").select("client_id, outstanding")
          .eq("business_id", bid)
          .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    for b in ob:
        cid = b.get("client_id")
        if cid:
            owed[cid] = owed.get(cid, Decimal(0)) + Decimal(str(b.get("outstanding") or 0))
    out = []
    for c in cl:
        nm = names.clean_display(c.get("name") or "")
        if not nm:
            continue
        o = owed.get(c["id"], Decimal(0))
        out.append({"client_id": c["id"], "name": nm, "outstanding": float(o)})
    out.sort(key=lambda x: (-x["outstanding"], x["name"].lower()))
    return {"clients": out}


@router.get("/admin/notifications")
def admin_notifications(token: str = Query(...), since: str = Query(""), v: int = Query(1)):
    """Money-in signal for the desktop 'ping'. Returns payment captures (customer
    said they paid / sent a screenshot) newer than `since` - the moment a shop
    owner most wants a nudge. The desktop app polls this and fires a native
    notification for each new one. Best-effort - always returns a usable shape."""
    biz = _biz_by_token(token)
    db = require_db()
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    events: list = []
    try:
        q = (db.table("pending_receipts")
             .select("id, party_display, party_ledger, amount, created_at, status")
             .eq("business_id", biz["id"])
             .in_("status", ["pending", "confirmed"])
             .order("created_at", desc=True).limit(20))
        if since:
            q = q.gt("created_at", since)
        rows = q.execute().data or []
        for r in rows:
            events.append({
                "id": r.get("id"),
                "party": r.get("party_display") or r.get("party_ledger") or "A customer",
                "amount": float(r.get("amount") or 0),
                "created_at": r.get("created_at"),
            })
    except Exception:
        log.debug("notifications fetch failed", exc_info=True)
    from app.services.templates import inr
    for e in events:
        e["kind"] = "capture"
        e["title"] = ("Money in: " + inr(e["amount"])) if e["amount"] else "A customer says they paid"
        e["body"] = e["party"] + " - open Payments to check and post to Tally."
    # Taps on the pay page ("X tapped Pay Rs5,276"). Only for shells that
    # understand the kind/title/body shape (v>=2): an older shell would render
    # a tap through its "Money in" template, which claims more than we know.
    open_expected = 0
    try:
        from app.services import pay_signals as _ps
        taps = _ps.open_taps(db, biz["id"])
        open_expected = len(taps)
        if v >= 2:
            fresh = [t for t in taps if not since or str(t.get("created_at") or "") > since]
            cids = list({t.get("client_id") for t in fresh if t.get("client_id")})
            nm = {}
            if cids:
                cr = (db.table("clients").select("id, name").in_("id", cids).execute()).data or []
                nm = {c["id"]: names.clean_display(c.get("name") or "") or "A customer" for c in cr}
            for t in fresh:
                amt = float(t.get("amount") or 0)
                events.append({
                    "id": t["id"], "kind": "tap",
                    "party": nm.get(t.get("client_id"), "A customer"), "amount": amt,
                    "created_at": t.get("created_at"),
                    "title": nm.get(t.get("client_id"), "A customer") + " tapped Pay " + inr(amt),
                    "body": ("For " + t["note"] + ". " if t.get("note") else "")
                            + "Check your bank or UPI app, then post it from Payments.",
                })
    except Exception:
        log.debug("tap notifications unavailable", exc_info=True)
    # Oldest-first so the app shows them in the order they arrived.
    events.sort(key=lambda e: str(e.get("created_at") or ""))
    return {"now": now_iso, "events": events, "count": len(events),
            "expected_open": open_expected}


class PromiseActionPayload(BaseModel):
    token: str
    client_id: str
    action: str                      # "chase" | "enqueue"
    amount: Optional[float] = None
    deposit_ledger: Optional[str] = None
    receipt_date: Optional[str] = None


@router.post("/admin/promises/action")
def admin_promise_action(payload: PromiseActionPayload):
    """Owner acts on an AI-read promise: 'chase' resumes reminders now; 'enqueue'
    queues a receipt (already confirmed) for the agent to post into Tally."""
    from app.services import promises as promises_svc, receipts_queue as rq
    biz = _biz_by_token(payload.token)
    db = require_db()
    bid = biz["id"]
    cr = (db.table("clients").select("id, name, tally_ledger_name")
          .eq("id", payload.client_id).eq("business_id", bid).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    client = cr.data[0]

    if payload.action == "chase":
        promises_svc.close_for_client(db, bid, client["id"], "cancelled")
        return {"ok": True, "resumed": True}

    if payload.action == "enqueue":
        amt = payload.amount
        if amt is None or float(amt) <= 0:
            ob = (db.table("bills").select("outstanding")
                  .eq("business_id", bid).eq("client_id", client["id"])
                  .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
            amt = float(sum(Decimal(str(b.get("outstanding") or 0)) for b in ob))
        if not amt or amt <= 0:
            raise HTTPException(status_code=400, detail="no amount to record")
        try:
            row = rq.create_pending(
                db, bid, client_id=client["id"],
                party_ledger=client.get("tally_ledger_name") or client["name"],
                party_display=names.clean_display(client["name"]), amount=amt,
                deposit_ledger=payload.deposit_ledger or "Cash",
                receipt_date=payload.receipt_date or None)
        except ValueError:
            raise HTTPException(status_code=400, detail="amount must be positive")
        if not row:
            # The one hard failure: the payment could not be saved at all. Give a
            # real reason so the owner is not left with a blank "Could not send".
            raise HTTPException(status_code=500,
                                detail="Could not save this payment. Check that ASVA is synced, then try again.")
        # From here the payment IS saved. Everything below is a best-effort finish
        # step and must NEVER turn a saved payment into an error the owner sees -
        # that is what made them re-enter the same payment again and again.
        confirmed = rq.confirm(db, bid, row["id"], amount=amt,
                               deposit_ledger=payload.deposit_ledger or "Cash",
                               receipt_date=payload.receipt_date or None)
        try:
            promises_svc.close_for_client(db, bid, client["id"], "kept")
        except Exception:
            log.warning("close_for_client failed after queueing receipt (business %s, client %s)",
                        bid, client["id"], exc_info=True)
        # An Expected row ("tapped Pay") for this party is answered by this.
        try:
            from app.services import pay_signals
            pay_signals.resolve_for_client(db, bid, client["id"], "owner")
        except Exception:
            log.debug("pay signal resolve skipped", exc_info=True)
        # queued=True either way; confirmed tells the UI whether it is already
        # heading to Tally or is sitting in "Ready to post" for one more tap.
        return {"ok": True, "queued": True, "confirmed": bool(confirmed)}

    raise HTTPException(status_code=400, detail="unknown action")


class PaySignalPayload(BaseModel):
    token: str
    id: str


@router.post("/admin/pay-signals/dismiss")
def admin_pay_signal_dismiss(payload: PaySignalPayload):
    """Owner pressed "Not received" on an Expected row: it leaves the list and
    becomes history on the party page. Nothing else changes."""
    from app.services import pay_signals
    biz = _biz_by_token(payload.token)
    db = require_db()
    ok = pay_signals.resolve(db, biz["id"], payload.id, "dismissed")
    return {"ok": bool(ok)}


class PendingConfirmPayload(BaseModel):
    token: str
    id: str
    amount: Optional[float] = None
    deposit_ledger: Optional[str] = None
    receipt_date: Optional[str] = None


@router.post("/admin/pending/confirm")
def admin_pending_confirm(payload: PendingConfirmPayload):
    """Owner approved a queued receipt (optionally editing amount/account/date):
    move it to 'confirmed' so the agent writes it into Tally."""
    from app.services import receipts_queue as rq
    biz = _biz_by_token(payload.token)
    db = require_db()
    try:
        row = rq.confirm(db, biz["id"], payload.id, amount=payload.amount,
                         deposit_ledger=payload.deposit_ledger,
                         receipt_date=payload.receipt_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="amount must be positive")
    if not row:
        raise HTTPException(status_code=404, detail="receipt not found or already handled")
    return {"ok": True}


class PendingCancelPayload(BaseModel):
    token: str
    id: str


@router.post("/admin/pending/cancel")
def admin_pending_cancel(payload: PendingCancelPayload):
    """Owner removed a queued receipt (won't post it to Tally)."""
    from app.services import receipts_queue as rq
    biz = _biz_by_token(payload.token)
    db = require_db()
    row = rq.get_by_id(db, biz["id"], payload.id)
    if not row:
        raise HTTPException(status_code=404, detail="receipt not found")
    rq.mark(db, payload.id, "skipped")
    return {"ok": True}


@router.get("/admin/accounts", response_class=HTMLResponse)
def admin_accounts(token: str = Query(...), lang: str = Query("english")):
    db = require_db()
    biz = (db.table("businesses")
           .select("id, business_name, upi_vpa, upi_vpa_2, upi_vpa_3, bank_account_name, bank_account_no, bank_ifsc, bank_name, share_data")
           .eq("agent_token", token).limit(1).execute())
    if not biz.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    b = biz.data[0]
    # Separate, forgiving read: migration 062 may not have run on this box yet,
    # and Settings must not 500 just because the notice fields are missing.
    b = {**_legal_fields(db, b["id"]), **b}
    share_on = bool(b.get("share_data", True))

    def val(k):
        return (b.get(k) or "").replace('"', "&quot;")

    body = f"""<h1>Accounts &amp; Payment</h1>
<div class="muted">{b['business_name']}</div>
<div class="card" style="margin-top:20px;max-width:560px">
 <label>UPI ID 1 (Default - reminder me QR + link isi ka jayega)</label>
 <input id="upi" value="{val('upi_vpa')}" placeholder="e.g. yourshop@okhdfcbank">
 
 <label>UPI ID 2 (Optional)</label>
 <input id="upi2" value="{val('upi_vpa_2')}" placeholder="e.g. yourshop@oksbi">
 
 <label>UPI ID 3 (Optional)</label>
 <input id="upi3" value="{val('upi_vpa_3')}" placeholder="e.g. yourshop@okaxis">
 <div class="hint" style="margin-top:8px">UPI set hai to har reminder me pay-link + QR apne aap lagta hai.</div>
 
 <label style="margin-top:20px">Bank account name</label>
 <input id="ban" value="{val('bank_account_name')}" placeholder="Your business name">
 <label>Account number</label>
 <input id="acc" value="{val('bank_account_no')}" placeholder="0000 0000 0000">
 <label>IFSC</label>
 <input id="ifsc" value="{val('bank_ifsc')}" placeholder="SBIN0000000">
 <label>Bank name</label>
 <input id="bank" value="{val('bank_name')}" placeholder="State Bank of India">
 <div class="hint">UPI na ho to reminder me ye bank details (A/C + IFSC) bheji jayengi.</div>
 <div style="margin-top:18px"><button onclick="save()">Save</button><span id="msg" class="okmsg"></span></div>
</div>
<div class="card" style="margin-top:16px;max-width:560px">
 <h3 style="margin:0 0 4px">Recovering old debts</h3>
 <div class="hint" style="margin:0">Only needed for a customer who is years past due. ASVA puts these on the demand notice it drafts for your advocate.</div>
 <label style="margin-top:16px">Udyam registration number</label>
 <input id="udyam" value="{val('udyam_number')}" placeholder="UDYAM-TN-00-0000000">
 <div class="hint" style="margin-top:8px">Registered as a micro or small enterprise? This lets you claim interest and file free at <b>odr.msme.gov.in</b>. Not registered? It is free at <b>udyamregistration.gov.in</b>.</div>
 <details style="margin-top:8px"><summary style="cursor:pointer;color:#1f6c9f;font-size:.86rem">Why this matters</summary>
  <div class="hint" style="margin-top:6px">Section 16 of the MSMED Act gives you interest at three times the RBI bank rate, compounded monthly, from the day payment was due. On a debt a few years old that is often more than the bill itself. Register before you need it: it does not revive a claim that has already run out of time.</div>
 </details>
 <label style="margin-top:16px">Shop address (as it should appear on a notice)</label>
 <textarea id="addr" rows="3" placeholder="12 Main Bazaar Road&#10;Chennai 600001" style="width:100%;font:inherit;padding:11px 12px;border:1px solid #EAEAEA;border-radius:8px;box-sizing:border-box">{val('shop_address')}</textarea>
 <div style="margin-top:18px"><button onclick="saveLegal()">Save</button><span id="lmsg" class="okmsg"></span></div>
</div>
<div class="card" style="margin-top:16px;max-width:560px">
 <label style="display:flex;align-items:center;gap:10px;cursor:pointer;margin:0;font-weight:600">
   <input type="checkbox" id="share" onchange="saveShare()" {'checked' if share_on else ''} style="width:18px;height:18px">
   Share anonymous payment data
 </label>
 <div class="hint" style="margin-top:8px">On by default. ASVA uses payment patterns only, no names and no bill details, to keep improving how it helps every shop. Turn it off and your data is never shared or used for others.<span id="smsg" class="okmsg"></span></div>
</div>
<script>
const TOKEN = {token!r};

// ── Busy guard ───────────────────────────────────────────────────────────
// The desktop shell reads window.__ASVA_DIRTY__ before it reloads this webview
// when a background Tally sync lands. A page is BUSY whenever a dialog is open
// or the owner is typing into something. Without this, a sync arriving while the
// owner was half-way through "Add a payment" reloaded the page under them: the
// dialog vanished and the typed name was gone, which reads as "the app will not
// let me enter anything".
function _asvaBusy(){{
  try{{
    var m = document.querySelectorAll('.cfback');
    for (var i=0;i<m.length;i++){{
      var d = m[i].style.display;
      if (d && d !== 'none') return true;
    }}
    var a = document.activeElement;
    if (a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.tagName==='SELECT')) return true;
  }}catch(e){{}}
  return false;
}}
setInterval(function(){{ try{{ window.__ASVA_DIRTY__ = _asvaBusy(); }}catch(e){{}} }}, 500);

async function saveShare() {{
  const on = document.getElementById('share').checked;
  const r = await fetch('/admin/accounts/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN, share_data: on}})}});
  const m = document.getElementById('smsg');
  m.textContent = r.ok ? '  Saved' : '  Failed';
  setTimeout(() => {{ m.textContent = ''; }}, 2500);
}}
async function saveLegal() {{
  const m = document.getElementById('lmsg'); m.textContent = 'Saving...';
  const r = await fetch('/admin/accounts/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN,
      udyam_number: document.getElementById('udyam').value,
      shop_address: document.getElementById('addr').value}})}});
  m.textContent = r.ok ? 'Saved' : 'Save failed';
  setTimeout(() => {{ m.textContent = ''; }}, 2500);
}}
async function save() {{
  document.getElementById('msg').textContent = 'Saving...';
  const r = await fetch('/admin/accounts/save', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token: TOKEN,
      upi_vpa: document.getElementById('upi').value,
      upi_vpa_2: document.getElementById('upi2').value,
      upi_vpa_3: document.getElementById('upi3').value,
      bank_account_name: document.getElementById('ban').value,
      bank_account_no: document.getElementById('acc').value,
      bank_ifsc: document.getElementById('ifsc').value,
      bank_name: document.getElementById('bank').value}})}});
  document.getElementById('msg').textContent = r.ok ? 'Saved' : 'Save failed';
}}
</script>"""
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<title>{b.get("business_name") or "ASVA"} - Accounts</title>{_FAVICON}'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{_topnav(token, lang, "accounts")}{body}</div>',
        lang))


class AccountsPayload(BaseModel):
    token: str
    upi_vpa: Optional[str] = None
    upi_vpa_2: Optional[str] = None
    upi_vpa_3: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None
    share_data: Optional[bool] = None
    category: Optional[str] = None
    default_credit_days: Optional[int] = None
    udyam_number: Optional[str] = None
    shop_address: Optional[str] = None


@router.post("/admin/accounts/save")
def admin_accounts_save(payload: AccountsPayload):
    from app.models import CATEGORY_CREDIT_DAYS, credit_days_for_category
    biz = _biz_by_token(payload.token)
    db = require_db()
    # Only update the fields actually sent, so the data-sharing toggle (which
    # posts share_data alone) never wipes the UPI/bank fields, and vice versa.
    update: dict = {}
    text_fields = {
        "upi_vpa": payload.upi_vpa, "upi_vpa_2": payload.upi_vpa_2,
        "upi_vpa_3": payload.upi_vpa_3, "bank_account_name": payload.bank_account_name,
        "bank_account_no": payload.bank_account_no, "bank_ifsc": payload.bank_ifsc,
        "bank_name": payload.bank_name,
        "udyam_number": payload.udyam_number, "shop_address": payload.shop_address,
    }
    for k, v in text_fields.items():
        if v is not None:
            cleaned = v.strip().upper() if k in ("bank_ifsc", "udyam_number") else v.strip()
            update[k] = cleaned or None
    if payload.share_data is not None:
        update["share_data"] = bool(payload.share_data)
    # Trade category -> sets the fallback credit period for parties Tally has no
    # BillCreditPeriod for. Picking a category seeds default_credit_days to that
    # trade's norm; an explicit default_credit_days overrides it.
    if payload.category is not None:
        cat = payload.category.strip().lower()
        if cat in CATEGORY_CREDIT_DAYS:
            update["category"] = cat
            if payload.default_credit_days is None:
                update["default_credit_days"] = credit_days_for_category(cat)
    if payload.default_credit_days is not None:
        try:
            d = int(payload.default_credit_days)
            if 1 <= d <= 365:
                update["default_credit_days"] = d
        except (TypeError, ValueError):
            pass
    if update:
        db.table("businesses").update(update).eq("id", biz["id"]).execute()
    return {"ok": True}
