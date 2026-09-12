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
from app.services import upi
from app.services.batches import bank_details
from app.services.templates import inr

log = logging.getLogger(__name__)
router = APIRouter(tags=["pay"])

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}

# Most banks cap one UPI transaction at 1 lakh. Above that the button will
# fail inside the customer's app with an unhelpful error, so say it first.
UPI_SOFT_LIMIT = 100_000

# Presets in percent of the ticked bills. Whole rupees, never below 1.
PRESETS = (10, 25, 50, 100)


def esc(text) -> str:
    return "".join(_ESCAPES.get(c, c) for c in str(text or ""))


_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Pay {shop}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#F7F6F3; color:#2F3437;
         font-family:'SF Pro Display',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         line-height:1.6; }}
  .wrap {{ max-width:420px; margin:0 auto; padding:28px 20px 56px; }}
  .card {{ background:#fff; border:1px solid #EAEAEA; border-radius:12px; padding:24px; }}
  .to {{ color:#787774; font-size:.78rem; letter-spacing:.08em; text-transform:uppercase; }}
  h1 {{ font-size:1.25rem; margin:.25rem 0 1.1rem; letter-spacing:-.02em; }}
  .amt {{ font-size:2.5rem; font-weight:700; letter-spacing:-.03em; margin:0;
          font-variant-numeric:tabular-nums; }}
  .ref {{ color:#787774; font-size:.95rem; margin:.25rem 0 0; }}
  .btn {{ display:block; text-align:center; text-decoration:none; margin-top:22px;
          background:#0a7d33; color:#fff; font-weight:600; font-size:1.05rem;
          padding:15px; border-radius:8px; }}
  .btn.off {{ background:#C9C7C2; pointer-events:none; }}
  .hint {{ color:#787774; font-size:.85rem; text-align:center; margin-top:10px; }}
  .sep {{ display:flex; align-items:center; gap:12px; color:#787774;
          font-size:.75rem; margin:26px 0 18px; text-transform:uppercase;
          letter-spacing:.08em; }}
  .sep::before, .sep::after {{ content:""; flex:1; height:1px; background:#EAEAEA; }}
  .qr {{ text-align:center; }}
  .qr img {{ width:210px; height:210px; image-rendering:pixelated; }}
  .row {{ display:flex; justify-content:space-between; gap:14px; padding:9px 0;
          border-bottom:1px solid #F2F1EE; font-size:.95rem; }}
  .row:last-child {{ border-bottom:0; }}
  .row b {{ font-weight:600; text-align:right; word-break:break-all;
            font-variant-numeric:tabular-nums; }}
  .row i {{ color:#787774; font-style:normal; }}
  .bill {{ align-items:center; cursor:pointer; margin:0; }}
  .bill input {{ width:20px; height:20px; margin:0 4px 0 0; flex:none; accent-color:#0a7d33; }}
  .bill .who {{ flex:1; min-width:0; }}
  .bill .who i {{ color:#2F3437; display:block; }}
  .bill .who small {{ color:#787774; font-size:.8rem; display:block; line-height:1.3; }}
  .bill .who small.late {{ color:#9F2F2D; }}
  .bill.unticked b, .bill.unticked .who i {{ color:#A8A6A1; }}
  .presets {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }}
  .presets button {{ font:inherit; font-weight:600; padding:10px 0; border-radius:8px;
                     border:1px solid #EAEAEA; background:#fff; color:#2F3437; cursor:pointer; }}
  .presets button.on {{ background:#2F3437; color:#fff; border-color:#2F3437; }}
  .presets button:focus-visible, .cust input:focus-visible {{ outline:2px solid #0a7d33; outline-offset:2px; }}
  .cust {{ display:flex; align-items:center; gap:8px; margin-top:10px;
           border:1px solid #EAEAEA; border-radius:8px; padding:6px 12px; }}
  .cust span {{ color:#787774; }}
  .cust input {{ font:inherit; font-size:1.1rem; font-weight:600; border:0; outline:0;
                 width:100%; min-width:0; font-variant-numeric:tabular-nums; }}
  .bal {{ font-size:.9rem; margin:12px 0 0; color:#2F3437; }}
  .bal.zero {{ color:#787774; }}
  .foot {{ color:#787774; font-size:.8rem; text-align:center; margin-top:24px; }}
  .warn {{ background:#FBF3DB; color:#956400; border-radius:8px; padding:12px;
           font-size:.9rem; margin-top:18px; }}
  details {{ margin-top:22px; border-top:1px solid #EAEAEA; }}
  summary {{ cursor:pointer; list-style:none; padding:14px 0 4px; font-size:.95rem;
             font-weight:600; display:flex; justify-content:space-between; align-items:center; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::after {{ content:"+"; color:#787774; font-weight:400; font-size:1.2rem; }}
  details[open] summary::after {{ content:"\2212"; }}
  summary:focus-visible {{ outline:2px solid #0a7d33; outline-offset:2px; }}
  .foot p {{ margin:0 0 8px; }}
</style></head><body><div class="wrap">
  <div class="card">
    <div class="to">Pay to</div>
    <h1>{shop}</h1>
    <p class="amt" id="amt">{amount}</p>
    {ref_html}
    {bills_html}
    {picker_html}
    {pay_html}
    {other_html}
  </div>
  <div class="foot">
    <p>After paying, send the screenshot to {shop} on WhatsApp so your account is updated.
       Bill looks wrong? Tell {shop} before paying.</p>
    <p>Sent by ASVA for {shop}. You pay {shop} directly.
       ASVA never collects or holds your money. This is a payment reminder, not a legal notice.</p>
  </div>
</div>{script}</body></html>"""


# Inline, dependency-free, old-Android-safe (var/function only). Kept out of
# the .format template so its braces need no doubling. __CFG__ is replaced
# with a json.dumps blob, never a Python repr.
_SCRIPT = """<script>
(function () {
  var C = __CFG__;
  var bills = [].slice.call(document.querySelectorAll('.bill input'));
  var presets = [].slice.call(document.querySelectorAll('.presets button'));
  var cust = document.getElementById('cust');
  var amtEl = document.getElementById('amt');
  var balEl = document.getElementById('bal');
  var btn = document.getElementById('paybtn');
  var qr = document.getElementById('qr');
  var big = document.getElementById('bigamt');
  var pct = 100, chosen = 0, qrTimer = null;

  function fmt(n) {
    // Indian grouping: 843000 -> 8,43,000
    var s = String(Math.round(n)), last = s.slice(-3), rest = s.slice(0, -3);
    if (rest) last = rest.replace(/\\B(?=(\\d{2})+(?!\\d))/g, ',') + ',' + last;
    return '\\u20B9' + last;
  }
  function ticked() {
    var out = [];
    bills.forEach(function (b) { if (b.checked) out.push(b); });
    return out;
  }
  function total() {
    var t = ticked();
    if (!bills.length) return C.total;
    var sum = 0;
    t.forEach(function (b) { sum += parseInt(b.getAttribute('data-p'), 10) || 0; });
    return sum / 100;
  }
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
  function money(n) { return (n % 1) ? n.toFixed(2) : String(n); }
  function render() {
    var full = total(), rest = full - chosen;
    if (rest < 0.5) rest = 0;
    amtEl.textContent = fmt(chosen);
    presets.forEach(function (p) {
      p.className = (parseInt(p.getAttribute('data-pct'), 10) === pct) ? 'on' : '';
    });
    if (cust && document.activeElement !== cust) cust.value = chosen ? money(chosen) : '';
    if (balEl) {
      if (!full) { balEl.textContent = 'Nothing selected. Tick a bill above.'; balEl.className = 'bal zero'; }
      else if (rest > 0) { balEl.innerHTML = 'Paying <b>' + fmt(chosen) + '</b> now. ' + (ticked().length > 1 ? 'These bills stay open' : 'The bill stays open') + ' and <b>' + fmt(rest) + '</b> remains due.'; balEl.className = 'bal'; }
      else { balEl.innerHTML = 'Paying <b>' + fmt(chosen) + '</b> now clears the selected bill' + (ticked().length > 1 ? 's' : '') + '.'; balEl.className = 'bal'; }
    }
    if (big) big.style.display = (chosen > C.softLimit) ? 'block' : 'none';
    if (btn) {
      var tn = note();
      btn.textContent = 'Pay ' + fmt(chosen) + ' by UPI';
      btn.className = chosen ? 'btn' : 'btn off';
      btn.setAttribute('href', chosen ? C.linkBase + '&am=' + chosen.toFixed(2) + '&tn=' + encodeURIComponent(tn) : '#');
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

  bills.forEach(function (b) {
    b.addEventListener('change', function () {
      b.parentNode.className = b.checked ? 'row bill' : 'row bill unticked';
      if (pct) fromPct(pct); else setChosen(chosen);
    });
  });
  presets.forEach(function (p) {
    p.addEventListener('click', function () { fromPct(parseInt(p.getAttribute('data-pct'), 10)); });
  });
  if (cust) {
    cust.addEventListener('input', function () {
      var v = parseInt(String(cust.value).replace(/[^0-9]/g, ''), 10);
      pct = 0;
      if (!isNaN(v)) { setChosen(v); } else { chosen = 0; render(); }
    });
    cust.addEventListener('blur', function () { setChosen(chosen); });
  }
  if (btn) {
    btn.addEventListener('click', function () {
      if (!chosen) return;
      // sendBeacon survives the page being replaced by the UPI app.
      try { navigator.sendBeacon('/pay/' + C.token + '/tapped?a=' + chosen.toFixed(2)); } catch (e) {}
    });
  }
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


def _log_event(db, business_id, client_id, kind: str, amount) -> None:
    """Record customer INTENT on the pay page. Best-effort and never fatal.

    This is not a payment confirmation and must never be shown as one: a UPI
    deep link hands the customer to their own app and nothing calls us back.
    It tells the owner "worth checking your bank now", hours before the
    customer gets round to replying PAID.
    """
    if db is None:
        return
    try:
        db.table("pay_link_events").insert({
            "business_id": business_id,
            "client_id": client_id,
            "kind": kind,
            "amount": float(amount or 0),
        }).execute()
    except Exception:
        log.debug("pay event %s not recorded", kind, exc_info=True)


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
# decide (bills, how much) and pay (one button). Everything else folds.

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
            '<label class="row bill"><input type="checkbox" checked data-p="'
            + str(paise) + '" data-n="' + esc(upi._clean_note(no)[:30]) + '">'
            '<span class="who"><i>' + esc(no) + "</i>"
            + ('<small class="' + ("late" if late else "") + '">' + esc(status) + "</small>" if status else "")
            + "</span><b>" + esc(inr(b.get("outstanding") or 0)) + "</b></label>")
    hint = ('<p class="hint" style="text-align:left;margin-top:6px">Untick any to pay later.</p>'
            if len(bills) > 1 else "")
    return ('<div class="sep">your pending ' + ("bill" if len(bills) == 1 else "bills")
            + "</div>" + "".join(rows) + hint)


def _picker_section(amount, has_vpa: bool, has_bank: bool) -> str:
    """How much now: presets of the ticked bills, or any whole-rupee amount.
    The balance line is the honest part: a part payment never reads as
    settled."""
    if float(amount) < 1:
        return ""
    more = ("use bank transfer under Other ways to pay, or " if (has_vpa and has_bank) else
            "use the bank transfer details below, or " if has_bank else "")
    return (
        '<div class="sep">how much now</div>'
        '<div class="presets">'
        + "".join('<button type="button" data-pct="' + str(p) + '"'
                  + (' class="on"' if p == 100 else "") + ">"
                  + ("Full" if p == 100 else str(p) + "%") + "</button>"
                  for p in PRESETS)
        + "</div>"
        '<label class="cust"><span>&#8377;</span>'
        '<input id="cust" inputmode="numeric" pattern="[0-9]*" '
        'placeholder="or type an amount" aria-label="Amount to pay now"></label>'
        '<p class="bal" id="bal"></p>'
        '<div class="warn" id="bigamt" style="display:none">Most UPI apps allow up to '
        + esc(inr(UPI_SOFT_LIMIT)) + " in one payment. For more, " + more
        + "pay in more than one UPI payment.</div>")


def _bank_rows(bank: dict) -> str:
    return "".join(
        '<div class="row"><i>' + esc(lbl) + "</i><b>" + esc(val) + "</b></div>"
        for lbl, val in (("Account name", bank["name"]),
                         ("Account number", bank["no"]),
                         ("IFSC", bank["ifsc"]),
                         ("Bank", bank["bank"]))
        if val)


def _pay_sections(vpa: str, shop: str, amount, ref: str, bank) -> tuple[str, str]:
    """(primary, other). With a VPA the primary is ONE button; the UPI ID, the
    QR and the bank details fold under "Other ways to pay" so the page stays
    calm. Without a VPA the bank details ARE the way to pay and stay open."""
    if vpa:
        link = upi.upi_link(vpa, shop, amount, ref or shop)
        primary = ('<a class="btn" id="paybtn" href="' + esc(link) + '">Pay '
                   + esc(inr(amount)) + " by UPI</a>"
                   '<p class="hint">Opens GPay, PhonePe, Paytm or your bank app.</p>')
        # The UPI ID in plain text as well: if the button cannot open an app
        # (an unusual phone, a desktop browser, an in-app webview), the customer
        # can still type this into their own UPI app. Never leave them with
        # only a button.
        other = '<div class="row"><i>UPI ID</i><b>' + esc(vpa) + "</b></div>"
        qr = upi.qr_png_base64(link)
        if qr:
            other += ('<div class="qr" style="margin-top:14px"><img id="qr" alt="UPI QR code for '
                      + esc(shop) + '" src="data:image/png;base64,' + qr + '">'
                      '<p class="hint">Scan from another phone. The code follows the amount you pick.</p>'
                      "</div>")
        if bank:
            other += ('<div class="sep">bank transfer</div>' + _bank_rows(bank)
                      + '<p class="hint">NEFT, RTGS or IMPS. Put the bill number in the remarks.</p>')
        return primary, ('<details><summary>Other ways to pay</summary>' + other + "</details>")
    if bank:
        primary = ('<div class="sep">pay by bank transfer</div>' + _bank_rows(bank)
                   + '<p class="hint">NEFT, RTGS or IMPS from your bank app. '
                     "Put the bill number in the remarks.</p>")
        return primary, ""
    return ('<div class="warn">This shop has not added a payment method yet. '
            "Please contact them directly.</div>"), ""


@router.post("/pay/{token}/tapped")
def pay_tapped(token: str, a: str = Query(default="")):
    """The customer pressed "Pay by UPI" and was handed to their UPI app.
    `a` is the amount they chose, so the owner's nudge can say it."""
    got = _amount_for(token, a)
    if not got:
        return {"ok": False}
    business_id, client_id, amount = got
    _log_event(get_client(), business_id, client_id, "tapped", amount)
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

    if bills:
        ref_html = ('<p class="ref">for ' + (str(len(bills)) + " bills" if len(bills) > 1
                                              else "1 bill") + "</p>")
    else:
        ref_html = '<p class="ref">Ref: ' + esc(ref) + "</p>" if ref else ""

    bills_html = stale_html + _bills_section(bills)
    picker_html = _picker_section(amount, has_vpa=bool(vpa), has_bank=bool(bank))
    pay_html, other_html = _pay_sections(vpa, shop, amount, ref, bank)

    cfg = {
        "token": token,
        "total": float(amount),
        "shop": upi._clean_note(shop)[:40],
        "ref": upi._clean_note(ref)[:40],
        "linkBase": upi.upi_link(vpa, shop) if vpa else "",
        "softLimit": UPI_SOFT_LIMIT,
        "qrDirty": False,
    }
    script = _SCRIPT.replace("__CFG__", json.dumps(cfg).replace("</", "<\\/"))

    return HTMLResponse(_PAGE.format(shop=esc(shop), amount=esc(inr(amount)),
                                     ref_html=ref_html, bills_html=bills_html,
                                     picker_html=picker_html, pay_html=pay_html,
                                     other_html=other_html, script=script))
