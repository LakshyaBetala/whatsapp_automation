"""Customer-facing payment page.

WhatsApp only linkifies http/https, so a raw "upi://pay?..." in a reminder
arrives as dead grey text the customer cannot tap. That single fact broke the
product's core loop: customers told shop owners "the link does not work".

This page is what the reminder links to instead. It carries the real UPI button,
the QR for paying from a second phone, and the bank details for NEFT/RTGS, so
every way a customer might pay sits on one screen. It is deliberately read-only:
ASVA never touches money, it only shows the shop's own collection details.

The token is signed (app.services.upi.pay_token), so a reminder costs no extra
database write and the URL cannot be edited to another shop or another amount.

Part payment. Kisht is how Indian trade actually pays, so the page lets the
customer choose WHICH bills and HOW MUCH now (10 / 25 / 50 / full, or any
amount). Two legal facts shape how that is presented:

  * Indian Contract Act s.59: a debtor who pays with an express intimation of
    which debt it is for is entitled to have it applied there. The bills the
    customer ticks go into the UPI transaction note ("INV 2526RTC0071 part"),
    so the customer's own bank record carries that intimation and the shop can
    honour it. The note is the customer's words, not ASVA's.
  * Limitation Act s.19: a part payment acknowledged in writing restarts the
    limitation clock on the debt. Same note, same benefit for the shop. The
    page never says "settled" or "cleared" for a part payment; the balance
    that stays due is stated in rupees next to the button.

The page is a reminder from the shop, never a legal notice, and never adds
interest or penalty. That line is printed on it.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response

from app.db import get_client
from app.services import pay_signals, upi
from app.services.batches import bank_details
from app.services.templates import inr

log = logging.getLogger(__name__)
router = APIRouter(tags=["pay"])

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}

# Most banks cap one UPI transaction at 1 lakh. Above that the button will
# fail inside the customer's app with an unhelpful error, so say it first.
UPI_SOFT_LIMIT = 100_000

# Presets in percent of the ticked bills. Whole rupees, never below 1.
PRESETS = (25, 50, 100)


def esc(text) -> str:
    return "".join(_ESCAPES.get(c, c) for c in str(text or ""))


_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex"><meta name="theme-color" content="#F7F6F3">
<title>Pay {shop}</title>
<style>
  :root {{ color-scheme: light; --ink:#1F2326; --mute:#787774; --line:#EAEAEA; --soft:#F2F1EE;
           --paper:#F7F6F3; --card:#fff; --go:#0a7d33; --late:#9F2F2D; }}
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html {{ background:var(--paper); }}
  body {{ margin:0; background:var(--paper); color:var(--ink); line-height:1.5;
         font-family:'SF Pro Display',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         padding-bottom:calc(112px + env(safe-area-inset-bottom)); }}
  body.nobar {{ padding-bottom:40px; }}
  .wrap {{ max-width:440px; margin:0 auto; padding:20px 16px 0; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:22px 18px; }}
  .to {{ color:var(--mute); font-size:.74rem; letter-spacing:.1em; text-transform:uppercase; }}
  h1 {{ font-size:1.15rem; margin:.2rem 0 1rem; letter-spacing:-.01em; line-height:1.3; }}
  .amt {{ font-size:2.6rem; font-weight:700; letter-spacing:-.035em; margin:0; line-height:1.1;
          font-variant-numeric:tabular-nums; transition:opacity .15s; }}
  .sub {{ color:var(--mute); font-size:.92rem; margin:.35rem 0 0; min-height:1.4em; }}
  .sub b {{ color:var(--ink); font-weight:600; }}
  .head {{ display:flex; justify-content:space-between; align-items:baseline; margin:26px 0 6px; }}
  .head span {{ color:var(--mute); font-size:.74rem; letter-spacing:.1em; text-transform:uppercase; }}
  .head button {{ font:inherit; font-size:.82rem; color:var(--go); background:none; border:0;
                  padding:6px 0 6px 12px; cursor:pointer; font-weight:600; }}
  .bill {{ display:flex; align-items:center; gap:12px; padding:12px 0; margin:0;
           border-bottom:1px solid var(--soft); cursor:pointer; }}
  .bill:last-of-type {{ border-bottom:0; }}
  .bill input {{ appearance:none; -webkit-appearance:none; width:24px; height:24px; margin:0; flex:none;
                 border:2px solid #C9C7C2; border-radius:7px; background:#fff; display:grid;
                 place-content:center; transition:background .12s, border-color .12s; }}
  .bill input::before {{ content:""; width:12px; height:7px; border:0 solid #fff; border-width:0 0 2.5px 2.5px;
                         transform:rotate(-45deg) translate(1px,-1px) scale(0); transition:transform .12s; }}
  .bill input:checked {{ background:var(--go); border-color:var(--go); }}
  .bill input:checked::before {{ transform:rotate(-45deg) translate(1px,-1px) scale(1); }}
  .bill input:focus-visible {{ outline:2px solid var(--go); outline-offset:2px; }}
  .bill .who {{ flex:1; min-width:0; }}
  .bill .who i {{ font-style:normal; display:block; font-weight:500; font-size:.97rem; }}
  .bill .who small {{ color:var(--mute); font-size:.8rem; display:block; line-height:1.3; }}
  .bill .who small.late {{ color:var(--late); }}
  .bill b {{ font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .bill.off .who i, .bill.off b {{ color:#B5B3AE; }}
  .bill.off .who small {{ color:#C9C7C2; }}
  .seg {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:8px; }}
  .seg button {{ font:inherit; font-weight:600; font-size:1rem; padding:13px 0; border-radius:10px;
                 border:1.5px solid var(--line); background:#fff; color:var(--ink); cursor:pointer;
                 transition:transform .08s; }}
  .seg button:active {{ transform:scale(.97); }}
  .seg button.on {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
  .seg button:focus-visible, .cust:focus-within {{ outline:2px solid var(--go); outline-offset:2px; }}
  .cust {{ display:flex; align-items:center; gap:8px; margin-top:10px; border:1.5px solid var(--line);
           border-radius:10px; padding:9px 14px; background:#fff; }}
  .cust.on {{ border-color:var(--ink); }}
  .cust span {{ color:var(--mute); font-size:1.05rem; }}
  .cust input {{ font:inherit; font-size:1.15rem; font-weight:600; border:0; outline:0; width:100%;
                 min-width:0; font-variant-numeric:tabular-nums; background:transparent; }}
  .bal {{ font-size:.9rem; margin:12px 0 0; color:var(--mute); }}
  .bal b {{ color:var(--ink); font-weight:600; }}
  .chip {{ display:inline-block; font:inherit; font-size:.86rem; font-weight:600; color:var(--go);
           background:#EDF3EC; border:0; border-radius:999px; padding:8px 14px; margin-top:10px; cursor:pointer; }}
  .chip:active {{ transform:scale(.97); }}
  .btn.alt {{ background:var(--ink); }}
  .warn {{ background:#FBF3DB; color:#956400; border-radius:10px; padding:11px 13px; font-size:.88rem; margin-top:14px; }}
  .row {{ display:flex; justify-content:space-between; gap:14px; padding:10px 0; border-bottom:1px solid var(--soft); font-size:.95rem; }}
  .row:last-child {{ border-bottom:0; }}
  .row i {{ color:var(--mute); font-style:normal; }}
  .row b {{ font-weight:600; text-align:right; word-break:break-all; font-variant-numeric:tabular-nums; }}
  .copy {{ font:inherit; font-size:.78rem; font-weight:600; color:var(--go); background:none; border:0;
           padding:0 0 0 8px; cursor:pointer; white-space:nowrap; }}
  .sep {{ color:var(--mute); font-size:.74rem; letter-spacing:.1em; text-transform:uppercase; margin:20px 0 8px; }}
  .qr {{ text-align:center; margin-top:14px; }}
  .qr img {{ width:200px; height:200px; image-rendering:pixelated; border:1px solid var(--line); border-radius:12px; padding:8px; background:#fff; }}
  .hint {{ color:var(--mute); font-size:.84rem; margin:8px 0 0; }}
  details {{ margin-top:20px; border-top:1px solid var(--line); }}
  summary {{ cursor:pointer; list-style:none; padding:16px 0 6px; font-size:.97rem; font-weight:600;
             display:flex; justify-content:space-between; align-items:center; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::after {{ content:""; width:9px; height:9px; border:solid var(--mute); border-width:0 2px 2px 0;
                    transform:rotate(45deg); margin:0 6px 4px 0; transition:transform .15s; }}
  details[open] summary::after {{ transform:rotate(-135deg); margin-bottom:-2px; }}
  summary:focus-visible {{ outline:2px solid var(--go); outline-offset:2px; }}
  .bar {{ position:fixed; left:0; right:0; bottom:0; background:rgba(247,246,243,.92);
          -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px); border-top:1px solid var(--line);
          padding:12px 16px calc(12px + env(safe-area-inset-bottom)); }}
  .bar .in {{ max-width:440px; margin:0 auto; }}
  .btn {{ display:block; text-align:center; text-decoration:none; background:var(--go); color:#fff;
          font-weight:600; font-size:1.08rem; padding:16px; border-radius:12px; transition:transform .08s, background .15s; }}
  .btn:active {{ transform:scale(.985); }}
  .btn.off {{ background:#C9C7C2; pointer-events:none; }}
  .bar .hint {{ text-align:center; margin-top:7px; font-size:.8rem; }}
  .foot {{ color:var(--mute); font-size:.8rem; text-align:center; margin:22px 0 0; line-height:1.5; }}
  .foot p {{ margin:0 0 6px; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style></head><body{body_class}><div class="wrap">
  <div class="card">
    <div class="to">Pay to</div>
    <h1>{shop}</h1>
    <p class="amt" id="amt">{amount}</p>
    <p class="sub" id="sub">{sub}</p>
    {bills_html}
    {picker_html}
    {inline_html}
    {other_html}
  </div>
  <div class="foot">
    <p>After paying, send the screenshot to {shop} on WhatsApp so your account is updated.</p>
    <p>You pay {shop} directly. ASVA never collects or holds your money.</p>
  </div>
</div>{bar_html}{script}</body></html>"""


# Inline, dependency-free, old-Android-safe (var/function only). Kept out of
# the .format template so its braces need no doubling. __CFG__ is replaced
# with a json.dumps blob, never a Python repr.
_SCRIPT = """<script>
(function () {
  var C = __CFG__;
  var bills = [].slice.call(document.querySelectorAll('.bill input'));
  var presets = [].slice.call(document.querySelectorAll('.seg button'));
  var cust = document.getElementById('cust');
  var custBox = document.getElementById('custbox');
  var amtEl = document.getElementById('amt');
  var subEl = document.getElementById('sub');
  var balEl = document.getElementById('bal');
  var btn = document.getElementById('paybtn');
  var qr = document.getElementById('qr');
  var big = document.getElementById('bigamt');
  var all = document.getElementById('all');
  var pct = 100, chosen = 0, qrTimer = null;

  function fmt(n) {
    // Indian grouping: 843000 -> 8,43,000
    var s = String(Math.round(n)), last = s.slice(-3), rest = s.slice(0, -3);
    if (rest) last = rest.replace(/\\B(?=(\\d{2})+(?!\\d))/g, ',') + ',' + last;
    return '\\u20B9' + last;
  }
  function money(n) { return (n % 1) ? n.toFixed(2) : String(n); }
  function ticked() {
    var out = [];
    bills.forEach(function (b) { if (b.checked) out.push(b); });
    return out;
  }
  function sumOf(list) {
    var s = 0;
    list.forEach(function (b) { s += parseInt(b.getAttribute('data-p'), 10) || 0; });
    return s / 100;
  }
  function total() { return bills.length ? sumOf(ticked()) : C.total; }
  function note() {
    // The customer's own words to the shop, carried in the UPI transaction
    // note: which bill, and whether it is the whole of it. Letters, digits,
    // spaces only (UPI apps drop the rest), max 50 chars.
    var t = ticked(), full = total(), n;
    if (bills.length && t.length === 1) n = 'INV ' + t[0].getAttribute('data-n');
    else if (bills.length && t.length > 1) n = t.length + ' bills';
    else n = C.ref || C.shop;
    if (chosen + 0.5 < full) n += ' part';
    return n.replace(/[^A-Za-z0-9 ._-]/g, '').slice(0, 50);
  }
  function setChosen(v) {
    // Part payments are whole rupees; "full" is the exact balance, paise and
    // all, so the customer is never asked for a rupee more than is due.
    var full = total();
    if (v >= full - 0.005) v = full;
    else v = Math.round(v);
    if (v < 1) v = full >= 1 ? 1 : 0;
    chosen = v;
    render();
  }
  function render() {
    var full = total(), rest = full - chosen, t = ticked();
    if (rest < 0.5) rest = 0;
    amtEl.textContent = fmt(chosen);
    if (subEl && bills.length) {
      var grand = sumOf(bills), nb = bills.length;
      if (!t.length) subEl.textContent = 'No bill selected';
      else if (t.length === nb && !rest) subEl.textContent = nb + (nb > 1 ? ' bills, ' : ' bill, ') + 'paying in full';
      else if (t.length === nb) subEl.innerHTML = 'of <b>' + fmt(grand) + '</b> across ' + nb + ' bills';
      else subEl.innerHTML = t.length + ' of ' + nb + ' bills selected' + (rest ? ', <b>' + fmt(full) + '</b> due on them' : '');
    }
    presets.forEach(function (p) {
      p.className = (parseInt(p.getAttribute('data-pct'), 10) === pct) ? 'on' : '';
    });
    if (custBox) custBox.className = pct ? 'cust' : 'cust on';
    if (cust && document.activeElement !== cust) cust.value = chosen ? money(chosen) : '';
    if (balEl) {
      if (!full) balEl.textContent = 'Tick a bill above to pay it.';
      else if (rest > 0) balEl.innerHTML = 'The ' + (t.length > 1 ? 'bills stay' : 'bill stays') + ' open. <b>' + fmt(rest) + '</b> remains due after this.';
      else balEl.innerHTML = 'This clears the selected ' + (t.length > 1 ? 'bills' : 'bill') + '.';
    }
    if (all) all.textContent = (t.length === bills.length) ? 'Clear all' : 'Select all';
    if (big) big.style.display = (chosen > C.softLimit) ? 'block' : 'none';
    suggest();
    if (btn) {
      var tn = note();
      if (overLimit()) {
        btn.textContent = 'Pay ' + fmt(chosen) + ' by bank transfer';
        btn.className = 'btn alt';
        btn.setAttribute('href', '#bank');
      } else {
        btn.textContent = chosen ? 'Pay ' + fmt(chosen) + ' by UPI' : 'Select a bill to pay';
        btn.className = chosen ? 'btn' : 'btn off';
        btn.setAttribute('href', chosen ? C.linkBase + '&am=' + chosen.toFixed(2) + '&tn=' + encodeURIComponent(tn) : '#');
      }
      if (qr) {
        clearTimeout(qrTimer);
        qrTimer = setTimeout(function () {
          if (chosen === C.total && !C.qrDirty) return;   // the inline QR already matches
          C.qrDirty = true;
          qr.src = '/pay/' + C.token + '/qr.png?a=' + chosen.toFixed(2) + '&n=' + encodeURIComponent(tn);
        }, 250);
      }
    }
  }
  function fromPct(p) { pct = p; setChosen(total() * p / 100); }
  function paise(b) { return parseInt(b.getAttribute('data-p'), 10) || 0; }
  function tickOnly(list) {
    bills.forEach(function (b) {
      var on = list.indexOf(b) >= 0;
      b.checked = on; b.parentNode.className = on ? 'bill' : 'bill off';
    });
  }
  function applyAmount(v) {
    // A typed amount picks the bills for the customer: an exact match to one
    // bill (or two) means that bill; otherwise oldest first, the way the shop
    // will apply it anyway, with the remainder as a part on the next one.
    pct = 0;
    if (!bills.length) { setChosen(v); return; }
    var want = Math.round(v * 100), grand = Math.round(sumOf(bills) * 100), i, j;
    if (want >= grand) { tickOnly(bills); setChosen(grand / 100); return; }
    for (i = 0; i < bills.length; i++) if (paise(bills[i]) === want) { tickOnly([bills[i]]); setChosen(v); return; }
    for (i = 0; i < bills.length; i++) for (j = i + 1; j < bills.length; j++)
      if (paise(bills[i]) + paise(bills[j]) === want) { tickOnly([bills[i], bills[j]]); setChosen(v); return; }
    var sel = [], run = 0;
    for (i = 0; i < bills.length; i++) {
      if (run + paise(bills[i]) <= want) { sel.push(bills[i]); run += paise(bills[i]); }
      else { if (run < want) sel.push(bills[i]); break; }
    }
    tickOnly(sel); setChosen(v);
  }
  function suggest() {
    // One nudge: the smallest top-up that clears one more bill. First over the
    // ticked bills in order, then the oldest bill not yet ticked.
    var el = document.getElementById('sugg');
    if (!el || !bills.length) return;
    var t = ticked(), want = Math.round(chosen * 100), run = 0, i, need = 0, k = 0, add = null;
    for (i = 0; i < t.length; i++) {
      run += paise(t[i]);
      if (run > want + 50) { need = run - want; k = i + 1; break; }
    }
    if (!need) {
      for (i = 0; i < bills.length; i++) if (!bills[i].checked) { add = bills[i]; break; }
      if (add) { need = paise(add); k = t.length + 1; }
    }
    if (!need || (chosen + need / 100 > C.softLimit && !C.hasBank)) { el.style.display = 'none'; return; }
    el.style.display = 'inline-block';
    el.textContent = 'Add ' + fmt(need / 100) + ' to clear ' + (k === 1 ? 'the oldest bill' : k + ' bills');
    el.onclick = function () {
      if (add) { add.checked = true; add.parentNode.className = 'bill'; }
      pct = 0; setChosen(chosen + need / 100);
    };
  }
  function overLimit() { return C.hasBank && chosen > C.softLimit; }

  bills.forEach(function (b) {
    b.addEventListener('change', function () {
      b.parentNode.className = b.checked ? 'bill' : 'bill off';
      if (pct) fromPct(pct); else setChosen(chosen);
    });
  });
  if (all) all.addEventListener('click', function () {
    var on = ticked().length !== bills.length;
    bills.forEach(function (b) { b.checked = on; b.parentNode.className = on ? 'bill' : 'bill off'; });
    if (pct) fromPct(pct); else setChosen(chosen);
  });
  presets.forEach(function (p) {
    p.addEventListener('click', function () { fromPct(parseInt(p.getAttribute('data-pct'), 10)); });
  });
  if (cust) {
    cust.addEventListener('input', function () {
      // Digits only, in the box as well as in the maths.
      var clean = String(cust.value).replace(/[^0-9]/g, '');
      if (clean !== cust.value) cust.value = clean;
      var v = parseInt(clean, 10);
      pct = 0;
      if (!isNaN(v)) { applyAmount(v); } else { chosen = 0; render(); }
    });
    cust.addEventListener('blur', function () { setChosen(chosen); });
  }
  if (btn) {
    btn.addEventListener('click', function (ev) {
      if (!chosen) return;
      if (overLimit()) {
        // Too big for one UPI payment and the shop takes bank transfers:
        // take the customer to those details instead of a failing intent.
        ev.preventDefault();
        var d = document.querySelector('details'); if (d) d.open = true;
        var bk = document.getElementById('bank'); if (bk) bk.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      // sendBeacon survives the page being replaced by the UPI app.
      try { navigator.sendBeacon('/pay/' + C.token + '/tapped?a=' + chosen.toFixed(2) + '&n=' + encodeURIComponent(note())); } catch (e) {}
    });
  }
  [].slice.call(document.querySelectorAll('.copy')).forEach(function (c) {
    c.addEventListener('click', function () {
      var v = c.getAttribute('data-v'), done = function () { c.textContent = 'Copied'; setTimeout(function () { c.textContent = 'Copy'; }, 1500); };
      try { navigator.clipboard.writeText(v).then(done, function () {}); } catch (e) {}
    });
  });
  if (qr) qr.addEventListener('error', function () { qr.parentNode.style.display = 'none'; });
  setChosen(total());
})();
</script>"""


def _simple(title: str, body: str) -> str:
    return ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + esc(title) + "</title>"
            '<body style="font-family:system-ui;max-width:380px;margin:70px auto;'
            'padding:0 20px;color:#2F3437;line-height:1.6">'
            '<h2 style="letter-spacing:-.02em">' + esc(title) + "</h2>"
            '<p style="color:#787774">' + esc(body) + "</p>")


def _log_event(db, business_id, client_id, kind: str, amount, note: str = "") -> None:
    """Record customer INTENT on the pay page. Best-effort and never fatal.

    This is not a payment confirmation and must never be shown as one: a UPI
    deep link hands the customer to their own app and nothing calls us back.
    It tells the owner "worth checking your bank now", hours before the
    customer gets round to replying PAID. See app.services.pay_signals for
    how a tap is later resolved.
    """
    pay_signals.record(db, business_id, client_id, kind, amount, note)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _bill_status(b: dict, today: date) -> tuple[str, bool]:
    """("Due 12 Aug, 31 days overdue", True). Factual, no interest, no threat."""
    due = _parse_date(b.get("due_date"))
    inv = _parse_date(b.get("invoice_date"))
    if due:
        late = (today - due).days
        text = "Due " + due.strftime("%d %b")
        if late > 0:
            return text + ", " + str(late) + (" day" if late == 1 else " days") + " overdue", True
        if late == 0:
            return text + ", due today", False
        return text, False
    if inv:
        return "Billed " + inv.strftime("%d %b"), False
    return "", False


def _load_bills(db, business_id: str, client_id: str) -> list[dict]:
    try:
        bills = (db.table("bills")
                 .select("invoice_number, outstanding, invoice_date, due_date")
                 .eq("business_id", business_id).eq("client_id", client_id)
                 .in_("status", ["pending", "partial", "overdue"])
                 .order("invoice_date").limit(50).execute()).data or []
    except Exception:
        log.exception("pay page bill list failed for client %s", client_id)
        return []
    return [b for b in bills if float(b.get("outstanding") or 0) > 0]


def _amount_for(token: str, requested) -> tuple | None:
    """(business_id, client_id, amount) for a QR or tap: the amount the
    customer chose on the page, falling back to the reminder's amount when
    the value is missing or junk. Only a valid signed token gets anything."""
    parsed = upi.parse_pay_token(token)
    if not parsed:
        return None
    business_id, amount, client_id = parsed
    try:
        chosen = Decimal(str(requested)) if requested not in (None, "") else amount
        if not (Decimal("0.01") <= chosen <= Decimal("100000000")):
            chosen = amount
    except Exception:
        chosen = amount
    return business_id, client_id, chosen.quantize(Decimal("0.01"))


# ── Page sections. Each returns HTML or "" so the page stays one screen:
# decide (bills, how much) and pay (one button that is always in reach).

def _bills_section(bills: list[dict]) -> str:
    """Every pending bill, tickable, with its factual due status."""
    if not bills:
        return ""
    today = date.today()
    rows = []
    for b in bills:
        paise = int(round(float(b.get("outstanding") or 0) * 100))
        no = str(b.get("invoice_number") or "Balance")
        status, late = _bill_status(b, today)
        rows.append(
            '<label class="bill"><input type="checkbox" checked data-p="'
            + str(paise) + '" data-n="' + esc(upi._clean_note(no)[:30]) + '">'
            '<span class="who"><i>' + esc(no) + "</i>"
            + ('<small class="' + ("late" if late else "") + '">' + esc(status) + "</small>" if status else "")
            + "</span><b>" + esc(inr(b.get("outstanding") or 0)) + "</b></label>")
    toggle = '<button type="button" id="all">Clear all</button>' if len(bills) > 1 else ""
    return ('<div class="head"><span>your pending ' + ("bill" if len(bills) == 1 else "bills")
            + "</span>" + toggle + "</div>" + "".join(rows))


def _picker_section(amount, has_vpa: bool, has_bank: bool) -> str:
    """How much now: presets of the ticked bills, or any whole-rupee amount.
    The balance line is the honest part: a part payment never reads as
    settled."""
    if float(amount) < 1:
        return ""
    more = ("use bank transfer under Other ways to pay, or " if (has_vpa and has_bank) else
            "use the bank transfer details below, or " if has_bank else "")
    return (
        '<div class="head"><span>how much now</span></div>'
        '<div class="seg">'
        + "".join('<button type="button" data-pct="' + str(p) + '"'
                  + (' class="on"' if p == 100 else "") + ">"
                  + ("Full" if p == 100 else str(p) + "%") + "</button>"
                  for p in PRESETS)
        + "</div>"
        '<label class="cust" id="custbox"><span>&#8377;</span>'
        '<input id="cust" inputmode="numeric" pattern="[0-9]*" '
        'placeholder="or type any amount" aria-label="Amount to pay now"></label>'
        '<p class="bal" id="bal"></p>'
        '<button type="button" class="chip" id="sugg" style="display:none"></button>'
        '<div class="warn" id="bigamt" style="display:none">Most UPI apps allow up to '
        + esc(inr(UPI_SOFT_LIMIT)) + " in one payment. For more, " + more
        + "pay in more than one UPI payment.</div>")


def _copy_row(label: str, value: str) -> str:
    return ('<div class="row"><i>' + esc(label) + '</i><b>' + esc(value)
            + '<button type="button" class="copy" data-v="' + esc(value) + '">Copy</button></b></div>')


def _bank_rows(bank: dict) -> str:
    return "".join(
        _copy_row(lbl, val)
        for lbl, val in (("Account name", bank["name"]),
                         ("Account number", bank["no"]),
                         ("IFSC", bank["ifsc"]),
                         ("Bank", bank["bank"]))
        if val)


def _pay_sections(vpa: str, shop: str, amount, ref: str, bank) -> tuple[str, str, str]:
    """(inline, other, bar). With a VPA the way to pay is ONE button, fixed at
    the bottom of the screen so it is in reach however long the bill list is;
    the UPI ID, the QR and the bank details fold under "Other ways to pay".
    Without a VPA the bank details ARE the way to pay and stay open in the
    card, and there is no bar."""
    if vpa:
        link = upi.upi_link(vpa, shop, amount, ref or shop)
        bar = ('<div class="bar"><div class="in">'
               '<a class="btn" id="paybtn" href="' + esc(link) + '">Pay '
               + esc(inr(amount)) + " by UPI</a>"
               '<p class="hint">Opens GPay, PhonePe, Paytm or your bank app</p>'
               "</div></div>")
        # The UPI ID in plain text as well: if the button cannot open an app
        # (an unusual phone, a desktop browser, an in-app webview), the customer
        # can still paste this into their own UPI app. Never leave them with
        # only a button.
        other = _copy_row("UPI ID", vpa)
        qr = upi.qr_png_base64(link)
        if qr:
            other += ('<div class="qr"><img id="qr" alt="UPI QR code for ' + esc(shop)
                      + '" src="data:image/png;base64,' + qr + '">'
                      '<p class="hint">Scan from another phone. The code follows the amount you pick.</p>'
                      "</div>")
        if bank:
            other += ('<div class="sep" id="bank">bank transfer</div>' + _bank_rows(bank)
                      + '<p class="hint">NEFT, RTGS or IMPS. Put the bill number in the remarks.</p>')
        return "", ('<details><summary>Other ways to pay</summary>' + other + "</details>"), bar
    if bank:
        inline = ('<div class="sep" style="margin-top:26px">pay by bank transfer</div>' + _bank_rows(bank)
                  + '<p class="hint">NEFT, RTGS or IMPS from your bank app. '
                    "Put the bill number in the remarks.</p>")
        return inline, "", ""
    return ('<div class="warn">This shop has not added a payment method yet. '
            "Please contact them directly.</div>"), "", ""


@router.post("/pay/{token}/tapped")
def pay_tapped(token: str, a: str = Query(default=""), n: str = Query(default="")):
    """The customer pressed "Pay by UPI" and was handed to their UPI app.
    `a` is the amount they chose and `n` the note their app was given (which
    bill, whole or part), so the owner's Expected row can say both."""
    got = _amount_for(token, a)
    if not got:
        return {"ok": False}
    business_id, client_id, amount = got
    _log_event(get_client(), business_id, client_id, "tapped", amount,
               note=upi._clean_note(n)[:60])
    return {"ok": True}


@router.get("/pay/{token}/qr.png")
def pay_qr(token: str, a: str = Query(default=""), n: str = Query(default="")):
    """QR for the amount the customer picked. The inline QR on the page covers
    the default; this serves every other choice."""
    got = _amount_for(token, a)
    if not got:
        return Response(status_code=404)
    business_id, _client_id, amount = got
    db = get_client()
    vpa = shop = ""
    if db is not None:
        try:
            rows = (db.table("businesses").select("business_name, upi_vpa")
                    .eq("id", business_id).limit(1).execute()).data or []
            if rows:
                shop = (rows[0].get("business_name") or "").strip()
                vpa = (rows[0].get("upi_vpa") or "").strip()
        except Exception:
            log.debug("pay qr lookup failed", exc_info=True)
    if not upi.looks_like_vpa(vpa):
        return Response(status_code=404)
    png = upi.qr_png_bytes(upi.upi_link(vpa, shop, amount, (n or "")[:50] or shop))
    if not png:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/pay/{token}", response_class=HTMLResponse)
def pay_page(token: str, ref: str = Query(default="")):
    """Render the shop's own collection details for one reminder amount."""
    parsed = upi.parse_pay_token(token)
    if not parsed:
        return HTMLResponse(
            _simple("Link not valid",
                    "This payment link is not valid or has been changed. Please ask "
                    "the shop to send it again."),
            status_code=404)

    business_id, amount, client_id = parsed
    db = get_client()
    rows = []
    if db is not None:
        try:
            rows = (db.table("businesses")
                    .select("business_name, upi_vpa, bank_account_name, "
                            "bank_account_no, bank_ifsc, bank_name")
                    .eq("id", business_id).limit(1).execute()).data or []
        except Exception:
            log.exception("pay page lookup failed for business %s", business_id)
            return HTMLResponse(
                _simple("Please try again",
                        "We could not load the payment details just now. Please open "
                        "the link again in a moment."),
                status_code=503)
    if not rows:
        return HTMLResponse(
            _simple("Link not valid", "This payment link is no longer active."),
            status_code=404)

    biz = rows[0]
    shop = (biz.get("business_name") or "the shop").strip()
    vpa = (biz.get("upi_vpa") or "").strip()
    # An account number typed into the UPI box would build a dead deep link and
    # a dead QR. Any UPI ID is name@handle whatever the bank (xyz@indianbank is
    # UPI just like xyz@ybl); anything else is not a VPA.
    if not upi.looks_like_vpa(vpa):
        vpa = ""
    bank = bank_details(biz)
    ref = (ref or "").strip()[:40]

    _log_event(db, business_id, client_id, "opened", amount)

    bills = _load_bills(db, business_id, client_id) if (client_id and db is not None) else []
    # A link lives in a chat for days. If the customer part-paid since it was
    # sent, the amount baked into the token is stale and would ask for money
    # they no longer owe. Show the LIVE balance instead. min() is right both
    # ways: a part payment drops the live total below the token amount, and
    # a batch discount puts the token amount below the live total - either
    # way the customer is never asked for more than is due.
    stale_html = ""
    live = sum(float(b.get("outstanding") or 0) for b in bills)
    if live > 0 and abs(live - float(amount)) >= 1:
        if live < float(amount):
            stale_html = ('<div class="warn">Part of this has already been received. '
                          "The balance below is up to date.</div>")
        amount = Decimal(str(min(live, float(amount))))

    # The line under the amount is live (the script rewrites it as bills are
    # ticked); this is only its first paint.
    if bills:
        sub = (str(len(bills)) + (" bills, " if len(bills) > 1 else " bill, ") + "paying in full")
    else:
        sub = ("Ref: " + esc(ref)) if ref else ""

    bills_html = stale_html + _bills_section(bills)
    picker_html = _picker_section(amount, has_vpa=bool(vpa), has_bank=bool(bank))
    inline_html, other_html, bar_html = _pay_sections(vpa, shop, amount, ref, bank)

    cfg = {
        "token": token,
        "total": float(amount),
        "shop": upi._clean_note(shop)[:40],
        "ref": upi._clean_note(ref)[:40],
        "linkBase": upi.upi_link(vpa, shop) if vpa else "",
        "softLimit": UPI_SOFT_LIMIT,
        "hasBank": bool(bank),
        "qrDirty": False,
    }
    script = _SCRIPT.replace("__CFG__", json.dumps(cfg).replace("</", "<\\/"))

    return HTMLResponse(_PAGE.format(shop=esc(shop), amount=esc(inr(amount)), sub=sub,
                                     bills_html=bills_html, picker_html=picker_html,
                                     inline_html=inline_html, other_html=other_html,
                                     bar_html=bar_html,
                                     body_class=("" if bar_html else ' class="nobar"'),
                                     script=script))
