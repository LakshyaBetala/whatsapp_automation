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
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.db import get_client
from app.services import upi
from app.services.batches import bank_details
from app.services.templates import inr

log = logging.getLogger(__name__)
router = APIRouter(tags=["pay"])

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


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
  .amt {{ font-size:2.5rem; font-weight:700; letter-spacing:-.03em; margin:0; }}
  .ref {{ color:#787774; font-size:.95rem; margin:.25rem 0 0; }}
  .btn {{ display:block; text-align:center; text-decoration:none; margin-top:22px;
          background:#0a7d33; color:#fff; font-weight:600; font-size:1.05rem;
          padding:15px; border-radius:8px; }}
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
  .row b {{ font-weight:600; text-align:right; word-break:break-all; }}
  .row i {{ color:#787774; font-style:normal; }}
  .foot {{ color:#787774; font-size:.8rem; text-align:center; margin-top:24px; }}
  .warn {{ background:#FBF3DB; color:#956400; border-radius:8px; padding:12px;
           font-size:.9rem; margin-top:18px; }}
</style></head><body><div class="wrap">
  <div class="card">
    <div class="to">Pay to</div>
    <h1>{shop}</h1>
    <p class="amt">{amount}</p>
    {ref_html}
    {upi_html}
    {bank_html}
  </div>
  <p class="foot">Sent by ASVA on behalf of {shop}.<br>
     You pay {shop} directly. ASVA never collects or holds your money.</p>
</div></body></html>"""


def _simple(title: str, body: str) -> str:
    return ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + esc(title) + "</title>"
            '<body style="font-family:system-ui;max-width:380px;margin:70px auto;'
            'padding:0 20px;color:#2F3437;line-height:1.6">'
            '<h2 style="letter-spacing:-.02em">' + esc(title) + "</h2>"
            '<p style="color:#787774">' + esc(body) + "</p>")


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

    business_id, amount = parsed
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
    bank = bank_details(biz)
    ref = (ref or "").strip()[:40]

    ref_html = '<p class="ref">Ref: ' + esc(ref) + "</p>" if ref else ""

    upi_html = ""
    if vpa:
        link = upi.upi_link(vpa, shop, amount, ref or shop)
        qr = upi.qr_png_base64(link)
        qr_html = ""
        if qr:
            qr_html = ('<div class="sep">or scan this code</div>'
                       '<div class="qr"><img alt="UPI QR code for ' + esc(shop) + '" '
                       'src="data:image/png;base64,' + qr + '">'
                       '<p class="hint">Scan from another phone with any UPI app.</p>'
                       "</div>")
        upi_html = ('<a class="btn" href="' + esc(link) + '">Pay ' + esc(inr(amount))
                    + " by UPI</a>"
                    '<p class="hint">Opens GPay, PhonePe, Paytm or your bank app.</p>'
                    + qr_html)

    bank_html = ""
    if bank:
        rows_html = "".join(
            '<div class="row"><i>' + esc(lbl) + "</i><b>" + esc(val) + "</b></div>"
            for lbl, val in (("Account name", bank["name"]),
                             ("Account number", bank["no"]),
                             ("IFSC", bank["ifsc"]),
                             ("Bank", bank["bank"]))
            if val)
        if rows_html:
            bank_html = ('<div class="sep">bank transfer</div>' + rows_html
                         + '<p class="hint">Use NEFT, RTGS or IMPS from your bank app.'
                           "</p>")

    if not upi_html and not bank_html:
        upi_html = ('<div class="warn">This shop has not added a payment method yet. '
                    "Please contact them directly.</div>")

    return HTMLResponse(_PAGE.format(shop=esc(shop), amount=esc(inr(amount)),
                                     ref_html=ref_html, upi_html=upi_html,
                                     bank_html=bank_html))
