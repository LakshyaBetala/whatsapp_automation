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
                    "discount_pct, plan, upi_vpa, whatsapp_number, reminder_cadence, "
                    "overdue_repeat_days, overdue_max_repeats")
            .eq("agent_token", token).limit(1).execute())
    if not resp.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return resp.data[0]


def _client_points(biz: dict, bill: dict, credit_days: int):
    """Cadence points for one bill using the SAME engine the sweep uses, so the
    dashboard's 'next reminder' is exactly what will actually send."""
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
    pts = cadence_points(
        cadence=biz.get("reminder_cadence") or DEFAULT_CADENCE,
        repeat_days=biz.get("overdue_repeat_days") or 7,
        max_repeats=biz.get("overdue_max_repeats") or 3,
        credit_days=credit_days or 30,
        due_offset=(due - inv).days,
    )
    return inv, pts


def _next_reminder(biz: dict, bills: list, credit_days: int, today: _dt.date):
    """(label, colour) for a party's next scheduled reminder across its open
    bills. Green = an upcoming nudge; amber = an overdue-track reminder; the
    date is invoice_date + cadence-day. No messages-table lookup (dashboard is
    a hot path); the detail page shows the exact sent/pending breakdown."""
    best_date = None
    best_kind = None
    for b in bills:
        inv, pts = _client_points(biz, b, credit_days)
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
        return "Sab reminder ho gaye", "#999"
    kd = "Aaj" if best_date <= today else best_date.strftime("%d %b")
    color = "#c77b0a" if best_kind in ("overdue", "escalate") else "#0a7d33"
    tag = "overdue" if best_kind in ("overdue", "escalate") else "reminder"
    return f"{kd} ({tag})", color


def _chunked(items: list, size: int = 100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
RELOAD_COOLDOWN_MIN = 10   # "Reload data" may be pressed once per 10 minutes


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
_UI_EN: list[tuple[str, str]] = [
    # ---- Dashboard ----
    ("Aapke customers, unki baaki aur naye bills. Tick = us party ko reminder jayega. Reminder timing badalni ho to baayein <b>Reminders</b> tab kholein.",
     "Your customers, their dues and new bills. A tick = that party gets reminders. To change reminder timing, open the <b>Reminders</b> tab on the left."),
    ("Har party ke aage <b>tick</b> = us party ko reminder jayega. Neeche se search / sort / Save list kar sakte ho. Non-Tally = photo/OCR se bane bills.",
     "A <b>tick</b> next to a party = that party gets reminders. Search / sort / save the list below. Non-Tally = bills made from a photo/OCR."),
    ("messages sent is month (auto-managed, not a limit you pay per).",
     "messages sent this month (auto-managed, not a per-message charge)."),
    ("active customers is month", "active customers this month"),
    ("Naya data aaya - dekhein", "New data arrived - view"),
    ("Tally bills", "Tally bills"),
    ("Non-Tally bills", "Non-Tally bills"),
    ("🔍 Naam se dhundo...", "🔍 Search by name..."),
    ("Baaki: zyada pehle", "Dues: highest first"),
    ("Overdue: zyada din pehle", "Overdue: most days first"),
    ("Naam: A to Z", "Name: A to Z"),
    ("Sabko reminder ON karo", "Turn reminders ON for all"),
    ("Sabko reminder OFF karo", "Turn reminders OFF for all"),
    ("✓ Sab ON", "✓ All ON"),
    ("✗ Sab OFF", "✗ All OFF"),
    ("💾 Save list", "💾 Save list"),
    ("Agla reminder", "Next reminder"),
    ("Send now", "Send now"),
    ("Sab reminder ho gaye", "All reminders done"),
    ("Credit days &amp; reminder schedule", "Credit days &amp; reminder schedule"),
    ("Tally se aaya hua, ya galat/khaali ho to yahan set karein.",
     "Comes from Tally; set it here if it is wrong or empty."),
    ("Reminder in dino par jayega (bill ke baad):", "Reminders go on these days (after the bill):"),
    ("Due date ke baad har ~", "After the due date, every ~"),
    ("din ek overdue reminder.", "days one overdue reminder."),
    ("(Timing ASVA khud set karta hai. Aap sirf credit days badal sakte hain.)",
     "(ASVA sets the timing itself. You only change the credit days.)"),
    ("Credit days 1 se 730 ke beech likhein.", "Enter credit days between 1 and 730."),
    ("Abhi ", "Send now to "),
    (" ko reminder bhejein?", " a reminder?"),
    ("Kitna payment mila? (Rs me)", "How much was received? (in Rs)"),
    ("Sahi amount likhein.", "Enter a valid amount."),
    ("Kuch apply nahi hua.", "Nothing was applied."),
    (" active customers hain - ", " active customers - "),
    (" active customers ke liye ", " active customers, "),
    ("/month) lein.", "/month)."),
    (" kaafi hai.", " is enough."),
    ("plan sahi hai.", "plan is correct."),
    ("Aapka ", "Your "),
    ("Aapke ", "You have "),
    ("ASVA suggestion: ", "ASVA suggestion: "),
    # ---- Reminders page ----
    ("Reminder timing ASVA khud manage karta hai (har party ke credit days ke hisaab se). Aap sirf yeh settings badlein.",
     "ASVA manages reminder timing itself (based on each party's credit days). You only change these settings."),
    ("Message language", "Message language"),
    ("Send reminders at", "Send reminders at"),
    ("Custom line", "Custom line"),
    ("(optional) e.g. Diwali greetings", "(optional) e.g. Diwali greetings"),
    ("Early-pay discount", "Early-pay discount"),
    ("QR + amount is discount se kam ho jayega. 0 = no discount.",
     "QR + amount drop by this discount. 0 = no discount."),
    ("Jo settings chuni hain, wahi message dikhega.", "Shows the message with the settings you chose."),
    ("View message", "View message"),
    ("Save settings", "Save settings"),
    ("Aaj se aage ki date pe tap karke holiday mark karein (red). Purani dates select nahi hongi. Marked dates par reminder skip hoga aur agle working day chala jayega. Save dabana zaroori hai; last saved list hi final hoti hai.",
     "Tap a future date to mark a holiday (red). Past dates cannot be selected. On marked dates reminders skip and move to the next working day. You must press Save; the last saved list is final."),
    ("Message preview", "Message preview"),
    ("Koi holiday nahi. Har din reminder ja sakta hai.", "No holidays. Reminders can go any day."),
    ("Holidays (in dino skip): ", "Holidays (skipped): "),
    ("Preview not available.", "Preview not available."),
    ("Preview failed.", "Preview failed."),
    # ---- Party page ----
    ("ASVA is party ke credit days ke hisaab se khud reminder bhejta hai.",
     "ASVA sends reminders itself based on this party's credit days."),
    ("Reminders abhi OFF hain. ON karne par schedule niche dikhega.",
     "Reminders are OFF. Turn them ON to see the schedule below."),
    ("Reminder schedule", "Reminder schedule"),
    ("Sabhi reminder ja chuke.", "All reminders already sent."),
    ("Ab tak <b>", "So far <b>"),
    ("</b> reminder gaye. Aane wale:", "</b> reminders sent. Upcoming:"),
    ("Reminder OFF karein", "Turn reminder OFF"),
    ("Reminder ON karein", "Turn reminder ON"),
    (" ke reminder OFF kar dein? Isko automatic reminder nahi jayega.",
     " - turn reminders OFF? It will get no automatic reminders."),
    ("Nahi ho paya.", "Could not do it."),
    ("Koi bill nahi.", "No bills."),
    ("Payments received (Tally)", "Payments received (Tally)"),
    ("Tally me is party ka koi receipt record nahi mila.",
     "No receipt recorded for this party in Tally."),
    ("Non-Tally party - payments dashboard se record hote hain.",
     "Non-Tally party - payments are recorded from the dashboard."),
    ("Total baaki", "Total outstanding"),
    ("Open bills", "Open bills"),
    ("Credit days", "Credit days"),
    ("aapko alert", "owner alert"),
    ("Aaj", "Today"),
    ("Sab ho gaye", "All done"),
    ("number nahi hai", "no number"),
    # ---- Analytics ----
    ("Aging (kitne din se baaki)", "Aging (days outstanding)"),
    ("Sabse zyada baaki (top 12)", "Highest dues (top 12)"),
    ("Parties owing", "Parties owing"),
    ("Overdue parties", "Overdue parties"),
    ("Open accounts", "Open accounts"),
    ("Not due", "Not due"),
    # ---- Accounts ----
    ("UPI ID (reminder me QR + link isi ka jayega)", "UPI ID (used for the QR + link in reminders)"),
    ("UPI set hai to har reminder me pay-link + QR apne aap lagta hai.",
     "If UPI is set, every reminder gets a pay-link + QR automatically."),
    ("Bank account name", "Bank account name"),
    ("Account number", "Account number"),
    ("Bank name", "Bank name"),
    ("UPI na ho to reminder me ye bank details (A/C + IFSC) bheji jayengi.",
     "If there is no UPI, these bank details (A/C + IFSC) are sent in reminders."),
    # ---- Shared / small ----
    ("Tally se sync:", "Tally sync:"),
    ("(har 5 min auto)", "(every 5 min, auto)"),
    ("Reload data", "Reload data"),
    ("Saving...", "Saving..."),
    ("Sending...", "Sending..."),
    ("Save failed", "Save failed"),
    ("Loading...", "Loading..."),
    ("Nahi mila", "Not found"),
    ("plan", "plan"),
    (" din", " days"),
    ("Baaki", "Balance"),
]


def _ui_translate(html: str, en: bool) -> str:
    if not en:
        return html
    for h, e in sorted(_UI_EN, key=lambda p: len(p[0]), reverse=True):
        if h != e:
            html = html.replace(h, e)
    return html


def _is_en(lang: str) -> bool:
    return (lang or "").strip().lower().startswith("en")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(token: str = Query(...), lang: str = Query("hinglish")):
    biz = _biz_by_token(token)
    db = require_db()

    synced_dt = _last_synced_at(db, biz["id"])
    synced_label = _fmt_ago(synced_dt)
    synced_iso = synced_dt.isoformat() if synced_dt else ""
    tally_label, tally_color = _tally_status(synced_dt)

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
    bills_by_client: dict[str, list] = {}
    start = 0
    while True:
        resp = (db.table("bills")
                .select("client_id, outstanding, due_date, invoice_date")
                .eq("business_id", biz["id"])
                .in_("status", ["pending", "partial", "overdue"])
                .range(start, start + 999).execute())
        batch = resp.data or []
        for b in batch:
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
        rem_on = c.get("reminders_enabled", True)
        checked = "checked" if rem_on else ""
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
        # Reminder status badge: Off, no-bills dash, or next scheduled send.
        cbills = bills_by_client.get(c["id"], [])
        if not rem_on:
            rem_badge = '<span class="rbadge off">OFF</span>'
        elif not cbills:
            rem_badge = '<span class="rbadge none">-</span>'
        else:
            rl, rc = _next_reminder(biz, cbills, cd_val, today)
            rem_badge = f'<span class="rbadge" style="color:{rc};border-color:{rc}">{rl}</span>'
        rows.append(
            f'<tr data-name="{cname.lower()}" data-amt="{float(out)}" data-od="{od}" data-src="{src}">'
            f'<td><input type="checkbox" class="cb" value="{c["id"]}" {checked}></td>'
            f'<td><a class="plink" href="/admin/party?token={token}&client_id={c["id"]}">{cname}</a></td>'
            f'<td class="amt">{out_str}</td>'
            f'<td class="od">{od_str}</td>'
            f'<td>{rem_badge}</td>'
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
 body{{font-family:system-ui,sans-serif;margin:16px;max-width:1080px;color:#222}}
 h2{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:12px}}
 .tablewrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
 table{{border-collapse:collapse;width:100%;min-width:760px}}
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
 .syncbar{{display:flex;align-items:center;gap:10px;margin:10px 0;font-size:.92em;color:#555;flex-wrap:wrap}}
 .syncbar b{{color:#222}}
 #reloadbtn{{padding:6px 14px;font-size:.9em;border:1px solid #0a7d33;color:#0a7d33;background:#fff;border-radius:6px}}
 #reloadbtn:disabled{{opacity:.5;cursor:default}}
 .dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}}
 #syncmsg{{color:#0a7d33}}
 .freshchip{{display:none;background:#fbf3db;color:#956400;border:1px solid #f0dfa8;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:.9em}}
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
 .plink{{color:#1f6c9f;text-decoration:none;font-weight:600}}
 .plink:hover{{text-decoration:underline}}
 .rbadge{{display:inline-block;font-size:.8em;border:1px solid #ccc;border-radius:12px;padding:2px 9px;white-space:nowrap}}
 .rbadge.off{{color:#999;border-color:#ddd;background:#f5f5f5}}
 .rbadge.none{{color:#bbb;border:0}}
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
<div class="tablewrap">
<table id="ptable"><tr><th>Reminder?</th><th>Party</th><th>Baaki</th><th>Overdue</th><th>Agla reminder</th><th>Credit days</th><th>WhatsApp</th><th>Actions</th></tr>
{''.join(rows)}
</table>
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

// Sync time + Tally status + Reload live in the app's top bar now (global).
// Here we only auto re-render when the 5-min auto-sync brings new data - and
// never while the owner is mid-edit (their next Save/Reload picks it up).
const SYNCED_AT = {synced_iso!r};   // ISO of last sync at page load (or "")
let DIRTY = false;
function markDirty() {{ DIRTY = true; }}
setInterval(async () => {{
  try {{
    const r = await fetch('/admin/sync-status?token=' + encodeURIComponent(TOKEN));
    const s = await r.json();
    if (s.last_synced_at && s.last_synced_at !== SYNCED_AT && !DIRTY) location.reload();
  }} catch (e) {{}}
}}, 90000);

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
  markDirty();
  document.querySelectorAll('tr[data-name]').forEach(r => {{
    if (r.style.display !== 'none') r.querySelector('.cb').checked = v;
  }});
}}
document.querySelectorAll('.cb').forEach(c => c.addEventListener('change', markDirty));
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
  if (r.ok) DIRTY = false;
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
    return HTMLResponse(_ui_translate(html, _is_en(lang)))


@router.get("/admin/reminders", response_class=HTMLResponse)
async def admin_reminders(token: str = Query(...), lang: str = Query("hinglish")):
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
 <div class="calwrap">
  <div class="calhead"><button onclick="calMove(-1)">&#9664;</button><span id="calLabel"></span><button onclick="calMove(1)">&#9654;</button></div>
  <div id="calGrid" class="calgrid"></div>
 </div>
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
    return HTMLResponse(_ui_translate(html, _is_en(lang)))


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


class ReloadPayload(BaseModel):
    token: str


@router.post("/admin/reload")
async def admin_reload(payload: ReloadPayload):
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
                    "detail": f"Abhi {wait} min baad dobara Reload kar sakte hain."}

    db.table("businesses").update({"refresh_requested_at": now.isoformat()}).eq("id", biz["id"]).execute()
    return {"ok": True, "requested_at": now.isoformat()}


@router.get("/admin/sync-status")
async def admin_sync_status(token: str = Query(...)):
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
    return {
        "last_synced_at": last.isoformat() if last else None,
        "last_synced_label": _fmt_ago(last),
        "pending_refresh": pending,
        "tally_label": tally_label,
        "tally_color": tally_color,
    }


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


class SetReminderPayload(BaseModel):
    token: str
    client_id: str
    enabled: bool


@router.post("/admin/set-reminder")
async def admin_set_reminder(payload: SetReminderPayload):
    """Turn reminders ON/OFF for ONE party (per-party page toggle). Removal is
    confirmed client-side before this is called."""
    biz = _biz_by_token(payload.token)
    db = require_db()
    cr = (db.table("clients").select("id, name")
          .eq("id", payload.client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    db.table("clients").update({"reminders_enabled": bool(payload.enabled)}).eq("id", payload.client_id).execute()
    return {"ok": True, "enabled": bool(payload.enabled)}


@router.get("/admin/party", response_class=HTMLResponse)
async def admin_party(token: str = Query(...), client_id: str = Query(...), lang: str = Query("hinglish")):
    """Per-party page: bills, payments received, and the exact reminder schedule
    (which nudges already went, which is next and when) - all fetched from Tally."""
    biz = _biz_by_token(token)
    db = require_db()
    today = _dt.date.today()

    cr = (db.table("clients")
          .select("id, name, whatsapp_number, credit_days, reminders_enabled, "
                  "tally_ledger_name, language")
          .eq("id", client_id).eq("business_id", biz["id"]).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="party not found")
    c = cr.data[0]
    cd_val = int(c.get("credit_days") or 0)
    is_tally = bool((c.get("tally_ledger_name") or "").strip())

    all_bills = (db.table("bills")
                 .select("id, invoice_number, amount, paid_amount, outstanding, invoice_date, "
                         "due_date, status, is_opening_balance")
                 .eq("business_id", biz["id"]).eq("client_id", client_id)
                 .order("invoice_date", desc=True).execute()).data or []
    open_bills = [b for b in all_bills if b["status"] in ("pending", "partial", "overdue")]
    open_total = sum(Decimal(str(b.get("outstanding") or 0)) for b in open_bills)

    # Payments recorded in Tally for this party (bill-wise history).
    receipts = []
    ledger_name = c.get("tally_ledger_name") or c.get("name")
    try:
        rr = (db.table("tally_receipts")
              .select("amount, receipt_date, tally_voucher_number")
              .eq("business_id", biz["id"]).eq("party_name", ledger_name)
              .order("receipt_date", desc=True).limit(20).execute())
        receipts = rr.data or []
    except Exception:
        receipts = []

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

    # ── Bills table ───────────────────────────────────────────────────
    bill_rows = ""
    for b in all_bills[:200]:
        odv = ""
        dd = b.get("due_date")
        if dd and b["status"] in ("pending", "partial", "overdue"):
            try:
                d = (today - _dt.date.fromisoformat(str(dd))).days
                if d > 0:
                    odv = f"{d} din"
            except (TypeError, ValueError):
                pass
        stcls = {"paid": "ok", "partial": "warn"}.get(b["status"], "due")
        bill_rows += (
            f'<tr><td>{esc(b.get("invoice_number") or "-")}</td>'
            f'<td>{esc(b.get("invoice_date"))}</td>'
            f'<td class="n">{_inr(b.get("amount"))}</td>'
            f'<td class="n">{_inr(b.get("paid_amount"))}</td>'
            f'<td class="n">{_inr(b.get("outstanding"))}</td>'
            f'<td>{esc(b.get("due_date") or "-")}</td>'
            f'<td><span class="tag {stcls}">{esc(b["status"])}</span></td>'
            f'<td class="n">{odv or "-"}</td></tr>'
        )
    if not bill_rows:
        bill_rows = '<tr><td colspan="8" class="muted">Koi bill nahi.</td></tr>'

    # ── Payments received ─────────────────────────────────────────────
    pay_rows = "".join(
        f'<tr><td>{esc(r.get("receipt_date"))}</td>'
        f'<td class="n">{_inr(r.get("amount"))}</td>'
        f'<td>{esc(r.get("tally_voucher_number") or "-")}</td></tr>'
        for r in receipts)
    if not pay_rows:
        pay_rows = ('<tr><td colspan="3" class="muted">'
                    + ("Tally me is party ka koi receipt record nahi mila." if is_tally
                       else "Non-Tally party - payments dashboard se record hote hain.")
                    + '</td></tr>')

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
    if rem_on:
        for b in open_bills:
            inv, pts = _client_points(biz, b, cd_val)
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

    # Compact forward schedule (only when ON).
    sched_section = ""
    if rem_on:
        chips = ""
        if goes_today:
            chips += f'<span class="chip next" title="{_KIND[goes_today]}">Aaj</span>'
        for d, k in upcoming:
            chips += f'<span class="chip {"over" if k in ("overdue","escalate") else "due"}">{d.strftime("%d %b")}</span>'
        if not chips:
            chips = '<span class="muted">Sabhi reminder ja chuke.</span>'
        sched_section = (
            f'<h2>Reminder schedule</h2><div class="card">'
            f'<div class="muted" style="margin-bottom:10px">Ab tak <b>{sent_count}</b> reminder gaye. Aane wale:</div>'
            f'<div class="chips">{chips}</div></div>')

    phone = c.get("whatsapp_number")
    phone_html = esc(phone) if phone else '<span style="color:#c0392b">number nahi hai</span>'
    toggle_label = "Reminder OFF karein" if rem_on else "Reminder ON karein"
    toggle_cls = "danger" if rem_on else "primary"
    src_tag = "Tally" if is_tally else "Non-Tally"
    rem_hint = ("ASVA is party ke credit days ke hisaab se khud reminder bhejta hai."
                if rem_on else
                "Reminders abhi OFF hain. ON karne par schedule niche dikhega.")

    body = f"""
<a href="/admin?token={token}" class="back">&#8592; Dashboard</a>
<h1>{esc(c['name'])}</h1>
<div class="muted">{src_tag} party &middot; {biz['business_name']}</div>

<div class="grid">
  <div class="card kpi"><div class="n">{_inr(open_total)}</div><div class="l">Total baaki</div></div>
  <div class="card kpi"><div class="n">{len(open_bills)}</div><div class="l">Open bills</div></div>
  <div class="card kpi"><div class="n">{cd_val or '-'}</div><div class="l">Credit days</div></div>
  <div class="card kpi"><div class="n" style="font-size:1rem;color:{next_color}">{next_label}</div><div class="l">Agla reminder</div></div>
</div>

<div class="card" style="margin-bottom:18px">
  <div class="remrow">
    <div>WhatsApp: <b>{phone_html}</b> &nbsp;&middot;&nbsp; Reminder:
      <b id="remstate" style="color:{'#0a7d33' if rem_on else '#c0392b'}">{'ON' if rem_on else 'OFF'}</b></div>
    <button id="remtoggle" class="{toggle_cls}" onclick="toggleRem()">{toggle_label}</button>
  </div>
  <div class="hint" id="remhint">{rem_hint}</div>
</div>

<h2>Bills</h2>
<div class="tablewrap"><table><tr><th>Bill</th><th>Date</th><th class="n">Amount</th><th class="n">Paid</th>
  <th class="n">Baaki</th><th>Due</th><th>Status</th><th class="n">Overdue</th></tr>{bill_rows}</table></div>

{sched_section}

<h2>Payments received (Tally)</h2>
<div class="tablewrap"><table><tr><th>Date</th><th class="n">Amount</th><th>Voucher</th></tr>{pay_rows}</table></div>

<script>
const TOKEN = {token!r};
const CID = {client_id!r};
const PNAME = {json.dumps(c["name"] or "")};
let REM_ON = {str(bool(rem_on)).lower()};
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
</script>"""
    extra = """
 .back{display:inline-block;margin-bottom:10px;color:#1f6c9f;text-decoration:none;font-weight:600}
 .remrow{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
 .tablewrap{overflow-x:auto}
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
"""
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}{extra}</style><div class="wrap">{body}</div>',
        _is_en(lang)))


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
async def admin_analytics(token: str = Query(...), lang: str = Query("hinglish")):
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
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{body}</div>', _is_en(lang)))


@router.get("/admin/accounts", response_class=HTMLResponse)
async def admin_accounts(token: str = Query(...), lang: str = Query("hinglish")):
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
    return HTMLResponse(_ui_translate(
        f'<!doctype html><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>{_CSS}</style><div class="wrap">{body}</div>', _is_en(lang)))


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
