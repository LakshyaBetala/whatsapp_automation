"""ASVA public marketing site - multi-page, SEO-first, server-rendered.

One shared shell (design system + sticky nav + footer + scroll reveal) wraps
every page. Pages: / , /how-it-works , /features , /pricing , /use-cases . Plus
/sitemap.xml , /robots.txt and /llms.txt (AI answer engines). Self-contained
except for a Google Fonts link (Inter + JetBrains Mono); everything else is
inline so it loads fast on a free static host.

House style: English, clear and to the point, NO em/en dashes anywhere. Visual
style: neo-brutalist ledger - bone canvas, ink-black 2px borders, hard offset
shadows, one bright-green accent, a monospace utility face for labels, figures
and commands. Edit CONTACT_WA / CONTACT_EMAIL.
"""
from __future__ import annotations

import json
import os
import shutil
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import (HTMLResponse, PlainTextResponse,
                               RedirectResponse, Response)

from app.config import settings

router = APIRouter(tags=["site"])

# When exporting the static site, _base() is pinned to the public website domain
# regardless of this app's own PUBLIC_BASE_URL. None = use settings.
_BASE_OVERRIDE: str | None = None

CONTACT_WA = "919344110272"           # ASVA's own WhatsApp (company/bot number)
CONTACT_EMAIL = "almmatix@gmail.com"
SITE_NAME = "ASVA"
TAGLINE = "Collect faster. Stop chasing."
# The i3 app domain that serves the actual installer file (downloads.py). The
# marketing /download page (SEO landing) sends people here to fetch the exe.
APP_BASE = "https://app.tryasva.com"
# The installer file itself. It is served with Content-Disposition: attachment,
# so linking straight to it downloads the exe immediately, no extra page hop.
DOWNLOAD_FILE = f"{APP_BASE}/download/ASVA-Setup.exe"
# The version of the installer being served, and a short honest changelog (newest
# first). Shown on the download page ("you are downloading X") and the What's new
# section. Bump DOWNLOAD_VERSION and prepend a row here on every shipped build.
DOWNLOAD_VERSION = "1.8.5"
VERSIONS = [
    ("1.8.5", "Speaks your language, finds the right party",
     "ASVA now replies in your chosen language, English or Hinglish, in the app and on WhatsApp. It finds the right party even from a short name or a small typo, keeps same-name shops apart, and asks you when it is unsure instead of guessing. Reply-reading is steadier, it remembers what each customer said, and a new RECOVERED command shows how much money came back this month."),
    ("1.8.4", "Knows when a customer promises to pay",
     "When a customer replies that they have paid, or promises a date, ASVA pauses that party's reminders and shows you exactly why on the party page, with the customer's own message. So you never chase someone who just told you they will pay."),
    ("1.8.3", "Steadier, and updates itself",
     "ASVA now updates on its own. When a new version is ready it downloads quietly and you just press Restart, with your data and WhatsApp still connected. This build also adds a dark mode, a clearer dashboard showing your party and outstanding count, a do-not-chase list, and a one tap connection check that tells you in plain words if anything needs fixing."),
    ("1.8", "Promise-to-Pay",
     "ASVA now reads your customer's WhatsApp reply. If they say they have paid, or promise a date like '5 tareek', it pauses the reminders and nudges you to record it in Tally, so you never chase someone who already paid."),
    ("1.7", "Morning checkpoint",
     "Before the day's reminders go out, ASVA shows you the list so you can hold anyone who already paid. Plus a welcome message for new shops and steadier one-code setup."),
    ("1.6", "One-click installer",
     "A single Windows installer with no keys to type. Type a short code, pick your Tally company, scan WhatsApp, and you are live in about five minutes."),
]
# Honestly labelled as not-yet-shipped.
COMING = [
    ("Reads payment screenshots", "Soon ASVA will read the amount off a UPI screenshot the customer sends, so recording it in Tally is one tap."),
]

# SEO keyword bank. Intent-led terms an Indian distributor (or an AI answering
# for one) would actually search: Tally + WhatsApp + collections/receivables.
KEYWORDS_DEFAULT = (
    "ASVA, Tally WhatsApp reminder, TallyPrime payment reminder, WhatsApp billing "
    "software India, automatic payment reminder app, accounts receivable automation "
    "India, debtor follow up software, outstanding collection software, send Tally "
    "invoice on WhatsApp, payment reminder for distributors, credit collection "
    "software, Tally add on WhatsApp, receivables management India, UPI payment "
    "reminder, wholesale billing WhatsApp, DSO reduction India, collection agent software"
)


def _base() -> str:
    if _BASE_OVERRIDE:
        return _BASE_OVERRIDE.rstrip("/")
    return (settings.public_base_url or "https://tryasva.com").rstrip("/")


def _wa(text: str) -> str:
    return f"https://wa.me/{CONTACT_WA}?text={quote(text)}"


WA_TRY = _wa("I want to try ASVA")

# ── design system (neo-brutalist ledger) ─────────────────────────────────────
CSS = """
:root{
 --bg:#f9f8f5;--paper:#fff;--ink:#0c1a10;--dark:#0b1a10;
 --muted:#37473c;--faint:#8a968d;
 --accent:#16a34a;--accent-d:#0f7a37;--wash:#eaf7ee;--wa:#25d366;
 --line:#0c1a10;--hair:#e2e6e0;
 --mono:'JetBrains Mono','SF Mono',ui-monospace,Consolas,monospace;
 --sans:'Inter','SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
 --maxw:1120px;--sh:4px 4px 0 var(--ink);
}
*{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:light;scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;overflow-x:hidden}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 24px}
a{color:var(--accent);text-decoration:none}a:hover{color:var(--accent-d)}
img{max-width:100%}
.mono{font-family:var(--mono)}
.eyebrow{font-family:var(--mono);font-size:.72rem;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--accent)}

@keyframes marquee{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
@keyframes glow{0%,100%{opacity:.45}50%{opacity:.85}}

/* NAV */
header.nav{position:sticky;top:0;z-index:50;background:rgba(249,248,245,.82);
  backdrop-filter:blur(12px);border-bottom:2px solid var(--ink)}
.nav .row{display:flex;align-items:center;gap:20px;height:64px}
.logo{display:flex;align-items:center;gap:9px;color:var(--ink);font-weight:900;
  font-size:1.25rem;letter-spacing:-.03em}
.logo .mark{width:30px;height:30px;background:var(--accent);border:2px solid var(--ink);
  border-radius:9px;display:grid;place-items:center;font-weight:900;color:#fff;
  box-shadow:2px 2px 0 var(--ink);font-size:.95rem}
.navlinks{display:flex;align-items:center;gap:4px;margin-left:auto}
.navlinks a{color:var(--ink);font-weight:600;font-size:.94rem;padding:6px 11px;border-radius:8px;
  border-bottom:2px solid transparent}
.navlinks a:hover{color:var(--accent-d)}
.navlinks a.on{border-bottom-color:var(--accent)}
.navcta{font-weight:800!important;font-size:.9rem;background:var(--accent);color:#fff!important;
  padding:9px 16px!important;border:2px solid var(--ink);border-radius:11px;box-shadow:3px 3px 0 var(--ink);
  transition:transform .12s,box-shadow .12s}
.navcta:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--ink);color:#fff!important}
@media(max-width:860px){.navlinks a.hidem{display:none}}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:8px;font-weight:800;font-size:1rem;
  padding:14px 24px;border:2px solid var(--ink);border-radius:13px;cursor:pointer;
  transition:transform .12s,box-shadow .12s}
.btn-p{background:var(--accent);color:#fff;box-shadow:5px 5px 0 var(--ink)}
.btn-p:hover{transform:translate(-2px,-2px);box-shadow:7px 7px 0 var(--ink);color:#fff}
.btn-s{background:var(--paper);color:var(--ink);box-shadow:5px 5px 0 var(--ink)}
.btn-s:hover{transform:translate(-2px,-2px);box-shadow:7px 7px 0 var(--ink)}
.cta-row{display:flex;gap:14px;flex-wrap:wrap}

/* Type */
h1{font-weight:900;letter-spacing:-.04em;line-height:.99;margin:0 0 20px;text-wrap:balance}
h2{font-weight:900;letter-spacing:-.035em;line-height:1.02;margin:0 0 10px;text-wrap:balance}
.hl{display:inline-block;background:var(--accent);color:#fff;padding:0 12px;border:2px solid var(--ink);
  border-radius:14px;transform:rotate(-2deg);box-shadow:4px 4px 0 var(--ink)}
.page-hero{padding:70px 0 24px;max-width:900px}
.page-hero .eyebrow{display:inline-block;margin-bottom:16px}
.page-hero h1{font-size:clamp(2.5rem,6.4vw,4.4rem)}
.lede{font-size:1.2rem;color:var(--muted);max-width:60ch;margin:0 0 28px;line-height:1.5}
.undernote{font-family:var(--mono);color:var(--muted);font-size:.85rem;margin-top:18px}
section{padding:52px 0}
.sechead{max-width:660px;margin-bottom:30px}
.sechead .eyebrow{display:block;margin-bottom:12px}
.sechead h2{font-size:clamp(1.8rem,4vw,2.9rem)}
.sechead p{color:var(--muted);margin:8px 0 0;font-size:1.05rem}
.muted{color:var(--muted)}
.morelink{display:inline-block;margin-top:22px;font-weight:800;color:var(--accent-d);font-size:.98rem}
.morelink:hover{color:var(--accent-d)}

/* Chips */
.chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px;font-family:var(--mono);
  font-size:.76rem;font-weight:700;color:var(--muted)}
.chips span{background:var(--paper);border:2px solid var(--ink);border-radius:9px;padding:7px 12px}
.badge{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:.72rem;
  font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:var(--wash);
  border:2px solid var(--accent);color:var(--accent-d);padding:6px 13px;border-radius:999px}
.badge .d{width:7px;height:7px;background:var(--accent);border-radius:50%}

/* Cards + grids */
.grid{display:grid;gap:16px}
.g2{grid-template-columns:repeat(2,1fr)}.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}.g5{grid-template-columns:repeat(5,1fr)}
@media(max-width:900px){.g3,.g4,.g5{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.g2,.g3,.g4,.g5{grid-template-columns:1fr}}
.card{background:var(--paper);border:2px solid var(--ink);border-radius:16px;padding:22px;
  box-shadow:var(--sh);transition:transform .14s,box-shadow .14s}
.card:hover{transform:translate(-2px,-3px);box-shadow:6px 7px 0 var(--accent)}
.card .ico{font-size:26px;line-height:1}
.card h3{margin:12px 0 7px;font-size:1.12rem;font-weight:800;letter-spacing:-.01em;
  display:flex;align-items:center;gap:9px}
.card h3 .dot{width:9px;height:9px;border-radius:3px;background:var(--accent);
  border:1px solid var(--ink);flex:none}
.card p{margin:0;color:var(--muted);font-size:.95rem;line-height:1.5}
.knum{font-family:var(--mono);font-size:.9rem;font-weight:700;color:var(--accent)}

/* Stats bar */
.stats{display:grid;grid-template-columns:repeat(4,1fr);border:2px solid var(--ink);
  border-radius:18px;overflow:hidden;background:var(--paper);box-shadow:var(--sh)}
.stats .s{padding:24px 20px;border-right:2px solid var(--ink)}
.stats .s:last-child{border-right:0}
.stats .n{font-size:clamp(1.7rem,3.4vw,2.1rem);font-weight:900;letter-spacing:-.03em}
.stats .l{font-size:.82rem;color:var(--muted);margin-top:5px;line-height:1.35}
@media(max-width:720px){.stats{grid-template-columns:1fr 1fr}
  .stats .s:nth-child(2){border-right:0}.stats .s:nth-child(1),.stats .s:nth-child(2){border-bottom:2px solid var(--ink)}}

/* Marquee */
.marq{border-top:2px solid var(--ink);border-bottom:2px solid var(--ink);background:var(--dark);
  color:var(--bg);overflow:hidden;padding:15px 0;margin:44px 0}
.marq .track{display:flex;width:max-content;animation:marquee 34s linear infinite;
  font-family:var(--mono);font-weight:700;font-size:.92rem;letter-spacing:.02em}
.marq .g{display:flex;gap:32px;padding-right:32px}
.marq .di{color:var(--wa)}

/* Phone mockup */
.mock{position:relative;width:300px;margin:0 auto}
.mock .halo{position:absolute;inset:-14px;background:radial-gradient(closest-side,rgba(22,163,74,.35),transparent);
  filter:blur(24px);animation:glow 3.4s ease-in-out infinite}
.mock .phone{position:relative;background:var(--dark);border:3px solid var(--ink);border-radius:38px;
  padding:12px;box-shadow:10px 12px 0 var(--ink);animation:floaty 5s ease-in-out infinite}
.mock .scr{background:#ded6cc;border-radius:28px;overflow:hidden}
.mock .top{background:#075e54;color:#fff;padding:13px 15px;display:flex;align-items:center;gap:10px}
.mock .top .av{width:34px;height:34px;border-radius:50%;background:var(--wa);display:grid;place-items:center;
  font-weight:900;font-size:.8rem;color:#063}
.mock .top .nm{font-weight:700;font-size:.86rem}.mock .top .st{font-size:.66rem;opacity:.85}
.mock .body{padding:15px 12px;display:flex;flex-direction:column;gap:9px;min-height:340px;background:#d9d0c6}
.bub{max-width:82%;font-size:.8rem;line-height:1.4;padding:9px 12px;border-radius:12px}
.bub.in{align-self:flex-start;background:#fff;color:var(--ink);border-bottom-left-radius:4px}
.bub.out{align-self:flex-end;background:#dcf8c6;color:var(--ink);border-bottom-right-radius:4px}
.mock .tag2{position:absolute;top:-16px;right:-22px;font-family:var(--mono);font-size:.68rem;font-weight:700;
  background:var(--accent);color:#fff;border:2px solid var(--ink);border-radius:999px;padding:6px 12px;
  transform:rotate(6deg);box-shadow:3px 3px 0 var(--ink)}

/* Dark message-example panel */
.darkband{background:var(--dark);color:var(--bg);border-top:2px solid var(--ink);border-bottom:2px solid var(--ink);
  padding:70px 0;margin:44px 0}
.darkband .eyebrow{color:var(--wa)}
.darkband h2{color:var(--bg)}
.darkband p.sub{color:#b7c6bc;font-size:1.05rem;max-width:54ch;margin:14px 0 0}
.msgs{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:28px}
@media(max-width:640px){.msgs{grid-template-columns:1fr}}
.msg{background:#0f2417;border:2px solid #1e3a29;border-radius:16px;padding:15px}
.msg .k{font-family:var(--mono);font-size:.68rem;color:var(--wa);font-weight:700;letter-spacing:.04em}
.msg .k.cust{color:#8fb6a0}
.msg .t{border-radius:12px;padding:10px 12px;margin-top:9px;font-size:.84rem;line-height:1.45}
.msg .t.out{background:#dcf8c6;color:var(--ink);border-bottom-right-radius:4px}
.msg .t.in{background:#fff;color:var(--ink);border-bottom-left-radius:4px}
.langpills{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px;font-family:var(--mono);font-size:.72rem;font-weight:700}
.langpills span{border:2px solid var(--wa);color:var(--wa);border-radius:999px;padding:6px 13px}

/* Command surface */
.cmd code{font-family:var(--mono);background:var(--wash);color:var(--accent-d);padding:2px 7px;
  border-radius:6px;font-weight:700;font-size:.82rem}
.cmd .line{padding:5px 0;font-size:.92rem;color:var(--muted)}

/* Compare table */
.compare{border:2px solid var(--ink);border-radius:18px;overflow:hidden;background:var(--paper);box-shadow:var(--sh)}
.compare table{width:100%;border-collapse:collapse;font-size:.9rem}
.compare thead tr{background:var(--dark);color:var(--bg);text-align:left;font-family:var(--mono);
  font-size:.72rem;letter-spacing:.04em}
.compare th{padding:14px 14px}.compare th.c{text-align:center}.compare th.us{color:var(--wa)}
.compare td{padding:13px 14px;border-top:2px solid var(--hair)}
.compare td.c{text-align:center;color:var(--faint)}
.compare td.yes{text-align:center;font-weight:900;color:var(--accent)}
.compare tbody tr:nth-child(even){background:#faf9f6}
.compare .ftnote{font-size:.76rem;color:var(--faint);padding:12px 14px;border-top:2px solid var(--hair)}

/* Pricing */
.plan{display:flex;flex-direction:column}
.plan.best{border-color:var(--accent);box-shadow:6px 6px 0 var(--accent)}
.plan.best:hover{box-shadow:8px 9px 0 var(--accent)}
.tag{font-family:var(--mono);font-size:.66rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--accent-d);height:16px;margin-bottom:6px}
.plan .name{font-weight:900;font-size:1.15rem}
.amt{font-family:var(--mono);font-size:2rem;font-weight:800;letter-spacing:-.02em;margin:10px 0 2px;
  font-variant-numeric:tabular-nums}
.amt.sm{font-size:1.4rem;margin-top:16px}
.amt .per{font-size:.82rem;color:var(--muted);font-weight:600}
.cap{font-family:var(--mono);font-size:.82rem;color:var(--ink);margin:2px 0 14px}
.plan ul{list-style:none;margin:0 0 18px;padding:0;flex:1}
.plan li{font-size:.9rem;color:var(--muted);padding:6px 0 6px 24px;position:relative}
.plan li::before{content:"";position:absolute;left:2px;top:11px;width:10px;height:10px;border-radius:3px;
  background:var(--wash);border:2px solid var(--accent)}
.plan li.no{color:var(--faint)}.plan li.no::before{background:#fbf3db;border-color:#e2c46a}
.buy{margin-top:auto;text-align:center;font-weight:800;font-size:.92rem;padding:11px;border-radius:11px;
  border:2px solid var(--ink);color:var(--ink);box-shadow:3px 3px 0 var(--ink);transition:transform .12s,box-shadow .12s}
.buy:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--ink);color:var(--ink)}
.plan.best .buy{background:var(--accent);color:#fff}
.plan.best .buy:hover{color:#fff}

/* Split panels */
.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.split{grid-template-columns:1fr}}
.panel{background:var(--wash);border:2px solid var(--ink);border-radius:16px;padding:24px;box-shadow:var(--sh)}
.panel .h{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--accent-d);margin-bottom:6px;font-weight:700}
.panel .big{font-weight:900;font-size:1.3rem;margin-bottom:10px}
.panel ul{margin:0;padding-left:18px}.panel li{color:var(--muted);font-size:.94rem;padding:3px 0}

/* Flow steps */
.flow{display:grid;gap:14px}
.flow .row{display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:start;background:var(--paper);
  border:2px solid var(--ink);border-radius:16px;padding:22px 24px;box-shadow:var(--sh)}
.flow .row .idx{font-family:var(--mono);font-weight:800;color:#fff;background:var(--accent);font-size:1rem;
  border:2px solid var(--ink);border-radius:11px;width:44px;height:44px;display:flex;align-items:center;
  justify-content:center;box-shadow:2px 2px 0 var(--ink)}
.flow .row h3{margin:2px 0 5px;font-size:1.14rem;font-weight:800;letter-spacing:-.01em}
.flow .row p{margin:0;color:var(--muted);font-size:.96rem}

/* FAQ */
.faq details{border:2px solid var(--ink);border-radius:14px;padding:16px 20px;margin-bottom:12px;
  background:var(--paper);box-shadow:var(--sh)}
.faq summary{cursor:pointer;font-weight:800;list-style:none;display:flex;justify-content:space-between;gap:12px}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";color:var(--accent);font-family:var(--mono);font-weight:800}
.faq details[open] summary::after{content:"-"}
.faq details p{margin:12px 0 0;color:var(--muted);font-size:.95rem}

/* CTA band */
.band{background:var(--dark);color:#fff;border:2px solid var(--ink);border-radius:20px;
  padding:56px 34px;text-align:center;margin:24px 0 56px;box-shadow:8px 8px 0 var(--accent)}
.band .eyebrow{color:var(--wa);display:block;margin-bottom:12px}
.band h2{color:#fff}.band p{color:#b8c6bd;max-width:540px;margin:0 auto 26px;font-size:1.05rem}

/* Footer */
footer.ft{border-top:2px solid var(--ink);padding:44px 0 56px}
.ft .cols{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap}
.ft .brand{max-width:280px}
.ft .brand .logo{margin-bottom:10px}
.ft .brand p{color:var(--muted);font-size:.92rem;margin:0}
.ft .col .h{font-family:var(--mono);font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink);margin-bottom:8px;font-weight:700}
.ft .col a{display:block;color:var(--muted);font-size:.92rem;padding:4px 0}
.ft .col a:hover{color:var(--accent-d)}
.ft .base{margin-top:30px;padding-top:18px;border-top:2px solid var(--hair);font-family:var(--mono);
  font-size:.8rem;color:var(--muted);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}

.reveal{opacity:0;transform:translateY(14px);transition:opacity .6s cubic-bezier(.16,1,.3,1),transform .6s cubic-bezier(.16,1,.3,1)}
.reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none;transition:none}html{scroll-behavior:auto}
  .marq .track,.mock .phone,.mock .halo{animation:none}
}

/* Accessibility: a visible focus ring, shown instantly (never animated) */
a:focus-visible,.btn:focus-visible,.buy:focus-visible,.navcta:focus-visible,summary:focus-visible{
  outline:3px solid var(--accent);outline-offset:3px;border-radius:11px}

/* "In plain words" strip - the one line a shopkeeper (or an AI) can quote */
.plain{background:var(--wash);border:2px solid var(--ink);border-radius:18px;padding:26px 28px;box-shadow:var(--sh)}
.plain .k{font-family:var(--mono);font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent-d)}
.plain p{margin:8px 0 0;font-size:clamp(1.12rem,2.3vw,1.5rem);font-weight:700;letter-spacing:-.02em;line-height:1.36;color:var(--ink)}
.plain p b{background:var(--accent);color:#fff;padding:0 6px;border-radius:6px;box-shadow:2px 2px 0 var(--ink);font-weight:800}

/* Dead-simple 3 steps */
.simple{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
@media(max-width:760px){.simple{grid-template-columns:1fr}}
.simple .st{background:var(--paper);border:2px solid var(--ink);border-radius:16px;padding:24px;box-shadow:var(--sh)}
.simple .st .n{font-family:var(--mono);font-weight:800;color:#fff;background:var(--ink);width:34px;height:34px;
  border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:.95rem}
.simple .st h3{margin:14px 0 6px;font-size:1.2rem;font-weight:800;letter-spacing:-.01em}
.simple .st p{margin:0;color:var(--muted);font-size:.96rem;line-height:1.5}

/* What you need row */
.req{display:flex;flex-wrap:wrap;gap:12px}
.req .r{display:flex;align-items:center;gap:10px;background:var(--paper);border:2px solid var(--ink);border-radius:12px;
  padding:12px 16px;box-shadow:3px 3px 0 var(--ink);font-weight:700;font-size:.95rem}
.req .r .e{font-size:1.2rem}

/* Roadmap "live / coming" tag */
.soon{display:inline-block;font-family:var(--mono);font-size:.62rem;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;background:#fbf3db;color:#8a6100;border:2px solid #e2c46a;border-radius:999px;
  padding:3px 10px;margin-bottom:12px}
.soon.live{background:var(--wash);color:var(--accent-d);border-color:var(--accent)}
"""

REVEAL_JS = """
<script>
(function(){var els=document.querySelectorAll('.reveal');
if(!('IntersectionObserver' in window)){els.forEach(function(e){e.classList.add('in')});return;}
var io=new IntersectionObserver(function(en){en.forEach(function(x){if(x.isIntersecting){x.target.classList.add('in');io.unobserve(x.target);}})},{rootMargin:'0px 0px -8% 0px'});
els.forEach(function(e){io.observe(e)});})();
</script>
"""

NAV = [("/", "Home"), ("/how-it-works", "How it works"),
       ("/features", "Features"),
       ("/use-cases", "Use cases")]


def _nav(active: str) -> str:
    links = "".join(
        f'<a class="hidem{" on" if p == active else ""}" href="{p}">{label}</a>'
        for p, label in NAV)
    return f"""<header class="nav"><div class="wrap"><div class="row">
  <a class="logo" href="/"><span class="mark">A</span>ASVA</a>
  <nav class="navlinks">{links}
    <a class="navcta" href="/download">Download app</a>
  </nav></div></div></header>"""


def _footer() -> str:
    return f"""<footer class="ft"><div class="wrap"><div class="cols">
  <div class="brand"><div class="logo"><span class="mark">A</span>ASVA</div>
    <p>The recovery agent for Indian distributors on TallyPrime. {TAGLINE}</p></div>
  <div class="col"><div class="h">Product</div>
    <a href="/how-it-works">How it works</a><a href="/features">Features</a>
    <a href="/use-cases">Use cases</a></div>
  <div class="col"><div class="h">Get started</div>
    <a href="/download">Download for Windows</a><a href="{WA_TRY}">Talk to us on WhatsApp</a>
    <a href="mailto:{CONTACT_EMAIL}">Email us</a></div>
</div>
<div class="base"><span>&copy; 2026 {SITE_NAME}. {TAGLINE}</span>
  <span><a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></span></div>
</div></footer>"""


FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900'
         '&family=JetBrains+Mono:wght@600;700&display=swap" rel="stylesheet">')


def page_shell(*, path: str, title: str, description: str, body: str,
               jsonld: str = "", keywords: str = "") -> str:
    canonical = _base() + (path if path != "/" else "/")
    og_img = _base() + "/og.png"
    ld = (f'<script type="application/ld+json">{jsonld}</script>' if jsonld else "")
    kw = f'<meta name="keywords" content="{keywords or KEYWORDS_DEFAULT}">'
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><meta name="theme-color" content="#16a34a">
<title>{title}</title>
<meta name="description" content="{description}">
{kw}
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta name="author" content="{SITE_NAME}">
<link rel="canonical" href="{canonical}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:type" content="website">
<meta property="og:locale" content="en_IN">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="ASVA - get paid on WhatsApp, straight from your Tally">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{og_img}">
{FONTS}
<style>{CSS}</style>{ld}
</head><body>
{_nav(path)}
<main>{body}</main>
{_footer()}
{REVEAL_JS}
</body></html>"""


# ── reusable content blocks ─────────────────────────────────────────────────
def _band(title: str, sub: str) -> str:
    return f"""<div class="wrap"><div class="band reveal">
  <span class="eyebrow">Get started</span><h2>{title}</h2><p>{sub}</p>
  <a class="btn btn-p" href="{WA_TRY}" style="background:var(--accent)">Talk to us on WhatsApp</a></div></div>"""


def _plain_strip() -> str:
    """One sentence, plain as it gets. This is what a shopkeeper reads to 'get it',
    and what an AI answer engine quotes when asked what ASVA is."""
    return f"""<div class="wrap"><div class="plain reveal">
  <div class="k">In plain words</div>
  <p>ASVA is a small helper on your Tally computer that sends your customers their bills and
    payment reminders on WhatsApp all day, so your money comes in <b>without a single phone call</b>.</p>
</div></div>"""


def _simple_steps() -> str:
    steps = [
        ("1", "Put it on your Tally computer",
         "Download one file, run it, and type the short code we read out to you on the phone. About 5 minutes, and there is nothing else to install."),
        ("2", "It messages your customers",
         "From your own WhatsApp number, in clear English (or Hindi, Gujarati and Marathi if you prefer). Bills go out with the PDF, reminders are polite and timed, and they stop the moment a customer replies."),
        ("3", "You get paid faster",
         "Payments update from Tally on their own. Every night ASVA sends you one WhatsApp: who paid today, and who to call."),
    ]
    cards = "".join(
        f'<div class="st"><div class="n">{n}</div><h3>{h}</h3><p>{p}</p></div>'
        for n, h, p in steps)
    return f'<div class="simple reveal">{cards}</div>'


def _requirements() -> str:
    items = [("\U0001f5a5️", "Windows 10 or 11 computer"),
             ("\U0001f4d8", "TallyPrime installed"),
             ("\U0001f4f1", "Your own WhatsApp")]
    r = "".join(f'<div class="r"><span class="e">{e}</span>{t}</div>' for e, t in items)
    return f'<div class="req reveal">{r}</div>'


def _phone_mock() -> str:
    bubbles = [
        ("out", "Hello \U0001f64f Bill #4021 from Sharma Traders, &#8377;18,400, due 15 Aug. Pay via UPI \U0001f447"),
        ("out", "Reminder: &#8377;18,400 is still pending on bill #4021."),
        ("in", "paisa 5 tareek ko bhej dunga sir \U0001f64f"),
        ("out", "Noted, thank you. Reminders paused, we will follow up around the 5th. ✅"),
    ]
    bub = "".join(f'<div class="bub {c}">{t}</div>' for c, t in bubbles)
    return f"""<div class="mock reveal">
  <div class="halo"></div>
  <div class="phone"><div class="scr">
    <div class="top"><div class="av">ST</div>
      <div><div class="nm">Sharma Traders</div><div class="st">via your shop &middot; online</div></div></div>
    <div class="body">{bub}</div>
  </div></div>
  <div class="tag2">reads replies, holds reminders</div>
</div>"""


def _stats_bar() -> str:
    stats = [
        ("&#8377;43L+", "receivables in the first live pilot"),
        ("1,966", "debtors handled in one Tally test"),
        ("30-160", "days of credit distributors carry"),
        ("Free", "for every shop till 15 Sep 2026"),
    ]
    cells = "".join(f'<div class="s"><div class="n">{n}</div><div class="l">{l}</div></div>'
                    for n, l in stats)
    return f'<div class="wrap"><div class="stats reveal">{cells}</div></div>'


def _marquee() -> str:
    items = ["PLUGIN-FREE TALLY", "SHOP'S OWN NUMBER", "UPI PAY LINKS",
             "HINDI · GUJARATI · MARATHI", "NEVER TOUCHES THE MONEY", "FIFO RECONCILE"]
    inner = ""
    for it in items:
        inner += f'<span>{it}</span><span class="di">&#9670;</span>'
    grp = f'<div class="g">{inner}</div>'
    return f'<div class="marq"><div class="track">{grp}{grp}</div></div>'


CYCLE = [
    ("01", "Reads Tally", "Pulls debtors, bills and receipts live from TallyPrime. No plugin installed."),
    ("02", "Sends the bill", "Every sale reaches the customer on WhatsApp with the real Tally PDF."),
    ("03", "Reminds on cadence", "Firm, polite follow-ups after the due date. One message per party."),
    ("04", "Takes payment", "UPI link and QR to the shop's own account. ASVA never holds the money."),
    ("05", "Reconciles", "Receipts clear the oldest bills first. The loop closes on its own."),
]


def _cycle_grid() -> str:
    cards = "".join(
        f'<div class="card"><div class="knum">{n}</div><h3>{h}</h3><p>{p}</p></div>'
        for n, h, p in CYCLE)
    return f'<div class="grid g5 reveal">{cards}</div>'


WHY = [
    ("From your own number", "Customers hear from your shop, not an unknown brand. Trust and relationships stay intact."),
    ("Timed, never spammy", "Reminders follow each party's credit terms and back off the moment they reply. No blasting, no bans."),
    ("Tally stays the truth", "ASVA never edits your books. It reads what is outstanding and confirms payments back from Tally."),
    ("End-of-day digest", "One clean WhatsApp summary each night: new bills, collections, and the accounts worth a call."),
]


def _why_grid() -> str:
    cards = "".join(
        f'<div class="card"><h3><span class="dot"></span>{h}</h3><p>{p}</p></div>'
        for h, p in WHY)
    return f'<div class="grid g2 reveal">{cards}</div>'


PLANS = [
    dict(name="Basic", price="&#8377;699", per="/mo", cap="Up to 300 debtors", best=False, tag="",
         feats=["Auto bills and reminders", "UPI pay links", "End-of-day digest"],
         no=["No owner assistant"], cta=("Start ASVA Basic", "Get started")),
    dict(name="Growth", price="&#8377;1,099", per="/mo", cap="Up to 500 debtors", best=True, tag="Most popular",
         feats=["Everything in Basic", "WhatsApp owner assistant", "Reminders and balances by chat", "Photo-bill capture"],
         no=[], cta=("Start ASVA Growth", "Get started")),
    dict(name="Pro", price="&#8377;1,999", per="/mo", cap="Up to 1,000 debtors", best=False, tag="",
         feats=["Everything in Growth", "Higher daily send volume", "Priority support"],
         no=[], cta=("Start ASVA Pro", "Get started")),
    dict(name="Custom", price="Let's talk", per="", cap="1,000+ debtors", best=False, tag="",
         feats=["Everything in Pro", "Multiple companies", "Onboarding done for you"],
         no=[], cta=("Custom ASVA plan", "Contact us")),
]


def _plan_card(pl: dict) -> str:
    amt = (f'<div class="amt">{pl["price"]}<span class="per">{pl["per"]}</span></div>'
           if pl["per"] else f'<div class="amt sm">{pl["price"]}</div>')
    feats = "".join(f"<li>{f}</li>" for f in pl["feats"])
    feats += "".join(f'<li class="no">{f}</li>' for f in pl["no"])
    tag = f'<div class="tag">{pl["tag"]}</div>' if pl["tag"] else '<div class="tag">&nbsp;</div>'
    msg, label = pl["cta"]
    return (f'<div class="card plan{" best" if pl["best"] else ""}">{tag}'
            f'<div class="name">{pl["name"]}</div>{amt}'
            f'<div class="cap">{pl["cap"]}</div><ul>{feats}</ul>'
            f'<a class="buy" href="{_wa(msg)}">{label}</a></div>')


def _pricing_grid() -> str:
    return f'<div class="grid g4 reveal">{"".join(_plan_card(p) for p in PLANS)}</div>'


# ── pages ───────────────────────────────────────────────────────────────────
def _home() -> str:
    body = f"""<div class="wrap">
 <section class="page-hero reveal" style="max-width:none;display:grid;grid-template-columns:1.12fr .88fr;gap:44px;align-items:center;padding-top:56px">
  <div>
   <span class="badge"><span class="d"></span> Windows app for TallyPrime</span>
   <h1 style="margin-top:20px">Send every bill and reminder<br>on WhatsApp, <span class="hl">by itself.</span></h1>
   <p class="lede">ASVA is a small app you install on the same Windows computer as your Tally.
     It reads your bills, messages your customers on WhatsApp from your own number, and gets you
     paid faster. You chase no one.</p>
   <div class="cta-row">
     <a class="btn btn-p" href="{DOWNLOAD_FILE}" download>Download for Windows</a>
     <a class="btn btn-s" href="/how-it-works">See how it works</a>
   </div>
   <div class="chips">
     <span>Windows 10 or 11</span><span>Works with TallyPrime</span><span>Your own WhatsApp number</span><span>5-min setup</span>
   </div>
  </div>
  {_phone_mock()}
 </section>
</div>
{_plain_strip()}
{_stats_bar()}
{_marquee()}
<div class="wrap">
 <section>
  <div class="sechead"><span class="eyebrow">How it works</span>
   <h2>Three steps, then it runs on its own.</h2>
   <p>No new habits. You keep using Tally and WhatsApp exactly as you do today.</p></div>
  {_simple_steps()}
  <a class="morelink" href="/how-it-works">See the full walkthrough &rarr;</a>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">What you need</span>
   <h2>If you can use WhatsApp, you can use ASVA</h2>
   <p>Nothing new to learn, and your customers install nothing.</p></div>
  {_requirements()}
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">Why ASVA</span>
   <h2>A recovery agent, not another reminder app</h2>
   <p>One job: getting your outstanding paid, without annoying your customers.</p></div>
  {_why_grid()}
  <a class="morelink" href="/features">Explore all features &rarr;</a>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">Free pilot</span>
   <h2>Free for every shop, till 15 September</h2>
   <p>Join the open pilot and use the full ASVA free until 15 September 2026. No card, no setup fee.
     Get your stuck money back first, decide later.</p></div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px">
    <a class="btn btn-p" href="/download">Download and start free</a>
    <a class="btn" href="{WA_TRY}">Talk to us on WhatsApp</a>
  </div>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">What's new</span>
   <h2>Shipping improvements, version after version.</h2>
   <p>The last few updates, and what is coming next. Your app updates itself, so you always have the latest.</p></div>
  {_whats_next()}
 </section>
</div>
{_band("See your first reminders go out today",
       "Send us a message and we will connect your Tally and set up your first batch together.")}"""
    graph = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "@id": _base() + "/#org", "name": SITE_NAME,
         "url": _base(), "email": CONTACT_EMAIL, "slogan": TAGLINE, "logo": _base() + "/og.png",
         "description": "ASVA is a Windows desktop app that connects to TallyPrime and sends bills and payment reminders on WhatsApp from the shop's own number, for Indian distributors."},
        {"@type": "WebSite", "@id": _base() + "/#website", "url": _base(),
         "name": SITE_NAME, "publisher": {"@id": _base() + "/#org"}, "inLanguage": "en-IN"},
        {"@type": "SoftwareApplication", "name": "ASVA",
         "applicationCategory": "BusinessApplication",
         "operatingSystem": "Windows 10, Windows 11",
         "description": "ASVA is a small Windows desktop app installed next to TallyPrime. It automatically sends bills and payment reminders on WhatsApp from the shop's own number and reconciles payments back to Tally, for Indian distributors selling on credit.",
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR",
                    "description": "Free for every shop until 15 September 2026"},
         "featureList": ["Tally sync", "WhatsApp bills and reminders",
                         "WhatsApp owner assistant", "UPI pay links",
                         "End-of-day digest", "Photo-bill capture"]},
    ]})
    return page_shell(
        path="/",
        title="ASVA - Windows app that collects your Tally payments on WhatsApp",
        description="ASVA is a small Windows app that runs next to TallyPrime and sends your bills and payment reminders on WhatsApp from your own number. Get paid faster, chase no one. Free for every shop till 15 September 2026.",
        keywords="Tally WhatsApp reminder, WhatsApp billing software India, automatic payment reminder, accounts receivable automation India, TallyPrime add on, collect outstanding payments, ASVA, Windows app for Tally",
        body=body, jsonld=graph)


def _how() -> str:
    parts = [
        ("ON THE SHOP'S PC", "The reader",
         "A small app sits next to Tally, reads the ledger, and delivers the server's queued messages from your own WhatsApp. It holds no keys."),
        ("IN THE CLOUD", "The brain",
         "Owns the schedule, send rules, audit trail and PDF. Decides what to send and when, and queues it for the shop to deliver."),
        ("ON THE CUSTOMER'S PHONE", "The channel",
         "Plain WhatsApp, from a number they already know, with a tap-to-pay UPI link straight to the shop's account."),
    ]
    pcards = "".join(
        f'<div class="card"><div class="knum" style="font-size:.72rem;letter-spacing:.06em">{k}</div>'
        f'<h3 style="margin-top:8px">{h}</h3><p>{p}</p></div>' for k, h, p in parts)

    rows = [
        ("1", "Connect TallyPrime",
         "A small ASVA app installs next to Tally on your Windows PC and reads it directly: sales, receipts, and each customer's outstanding balance. It is read-only. Your books never leave your machine and nothing is uploaded to the cloud."),
        ("2", "Bills go out on WhatsApp",
         "The moment a new sales bill is raised in Tally, ASVA sends it to that customer on WhatsApp with the PDF attached, from your own number. If you already export a bill from Tally, ASVA picks it up and delivers it."),
        ("3", "Reminders chase the overdue accounts",
         "ASVA works out who is overdue using each party's credit terms and sends polite, timed reminders with a UPI pay link. It respects a daily limit, spaces messages out like a human, and stops the moment a customer replies."),
        ("4", "Payments reconcile from Tally",
         "When you record a receipt in Tally, ASVA reads it and marks the right bills paid, oldest first. You never update two places. Tally stays the single source of truth."),
        ("5", "You stay in control on WhatsApp",
         "Every night you get a digest: new bills, money collected, and the accounts worth a call. On Growth and above you can ask ASVA by chat: list debtors, check a party's balance, or send a reminder on demand."),
    ]
    flow = "".join(
        f'<div class="row"><div class="idx">{i}</div><div><h3>{h}</h3><p>{p}</p></div></div>'
        for i, h, p in rows)

    msgs = [
        ("BILL SENT", "out", "Hello \U0001f64f Your bill from Sharma Traders: #4021, &#8377;18,400, due 15 Aug. PDF attached. Pay via UPI \U0001f447"),
        ("REMINDER · DUE DATE", "out", "A reminder from Sharma Traders: &#8377;18,400 is pending on bill #4021. Kindly clear it at your convenience \U0001f447"),
        ("CUSTOMER REPLIES", "in", "ok sir, sending the payment today \U0001f64f<br><span style='color:#8fb6a0;font-size:.76rem'>reminders auto-pause</span>"),
        ("PAYMENT CONFIRMED", "out", "Payment received ✅ &#8377;18,400. Thank you!"),
    ]
    msgcards = "".join(
        f'<div class="msg"><div class="k{" cust" if k=="CUSTOMER REPLIES" else ""}">{k}</div>'
        f'<div class="t {c}">{t}</div></div>' for k, c, t in msgs)

    owner_cmds = [("LIST", "open debtors and what each owes"),
                  ("REMIND TOP 10", "chase the ten biggest right now"),
                  ("[photo]", "snap a paper bill, ASVA reads it"),
                  ("TERMS Ramesh 45", "set a party's credit days"),
                  ("DIGEST", "today's summary, who was reminded")]
    cust_cmds = [("HISAB", "see my full outstanding"),
                 ("PAID", "tell the shop I have paid, owner confirms"),
                 ("band karo", "stop reminders, honoured at once")]
    ocmd = "".join(f'<div class="line"><code>{c}</code> {d}</div>' for c, d in owner_cmds)
    ccmd = "".join(f'<div class="line"><code>{c}</code> {d}</div>' for c, d in cust_cmds)

    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">How it works</span>
  <h1>Three parts. Your WhatsApp never leaves your PC.</h1>
  <p class="lede">Each part does only its job. The shop's WhatsApp stays on the shop's own
    connection, so the number is never at risk.</p>
  <div class="cta-row"><a class="btn btn-p" href="{DOWNLOAD_FILE}" download>Download for Windows</a>
    <a class="btn btn-s" href="/features">See the features</a></div>
 </section>
 <section><div class="grid g3 reveal">{pcards}</div></section>
</div>
<div class="darkband"><div class="wrap">
  <div style="display:grid;grid-template-columns:.9fr 1.1fr;gap:44px;align-items:center">
   <div class="reveal">
    <span class="eyebrow">What it actually sends</span>
    <h2 style="margin-top:14px">Real messages that read like you sent them.</h2>
    <p class="sub">Bills, reminders and thank-yous in clear English, or Hindi, Gujarati and Marathi if you
      prefer. All from your own number, so they never look like template spam.</p>
    <div class="langpills"><span>ENGLISH</span><span>HINDI</span><span>GUJARATI</span><span>MARATHI</span></div>
   </div>
   <div class="msgs reveal">{msgcards}</div>
  </div>
</div></div>
<div class="wrap">
 <section>
  <div class="sechead"><span class="eyebrow">The command surface</span>
   <h2>Run the whole thing by texting.</h2>
   <p>No dashboard to log into. The owner texts the assistant; the customer self-serves on the same chat.</p></div>
  <div class="split reveal cmd">
   <div class="card"><h3>Owner commands</h3>{ocmd}</div>
   <div class="card"><h3>Customer commands</h3>{ccmd}
     <div class="line" style="margin-top:8px">Bilingual by default. The customer never installs anything or learns a command. The reminder itself invites a one-word reply.</div></div>
  </div>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">Step by step</span><h2>From connecting Tally to getting paid</h2></div>
  <div class="flow reveal">{flow}</div>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">What runs where</span>
   <h2>Your data stays on your PC</h2>
   <p>ASVA is split on purpose. Your books stay local. Only the messages and reminders run in the cloud.</p></div>
  <div class="split reveal">
   <div class="panel"><div class="h">On your PC</div><ul>
     <li>TallyPrime, exactly as you use it today</li>
     <li>A small ASVA app that reads Tally, read-only</li>
     <li>Your WhatsApp, sending from your own number</li>
     <li>Your ledger and PDFs, never uploaded</li></ul></div>
   <div class="panel"><div class="h">In the ASVA cloud</div><ul>
     <li>Reminder scheduling and daily send limits</li>
     <li>The end-of-day digest and owner assistant</li>
     <li>Your subscription and usage</li>
     <li>The dashboard we use to support you</li></ul></div>
  </div>
 </section>
</div>
{_band("Ready to see it on your Tally?",
       "We will install ASVA with you and send your first batch of bills and reminders together.")}"""
    howto = json.dumps({
        "@context": "https://schema.org", "@type": "HowTo",
        "name": "How ASVA collects outstanding payments from Tally on WhatsApp",
        "description": "Connect TallyPrime, send bills and timed reminders on WhatsApp from your own number, and reconcile payments from Tally.",
        "step": [{"@type": "HowToStep", "position": i, "name": h, "text": p}
                 for i, (_, h, p) in enumerate(rows, 1)],
    })
    crumbs = _breadcrumb("How it works", "/how-it-works")
    return page_shell(
        path="/how-it-works",
        title="How ASVA works | Tally to WhatsApp collections, step by step",
        description="How ASVA works: connect TallyPrime, send bills and timed reminders on WhatsApp from your own number, reconcile payments from Tally, and stay in control with a nightly digest.",
        keywords="how to send Tally invoice on WhatsApp, Tally WhatsApp integration, automatic payment reminder Tally, reconcile Tally payments, WhatsApp reminder workflow",
        body=body, jsonld=howto + '</script><script type="application/ld+json">' + crumbs)


def _features() -> str:
    cards_data = [
        ("\U0001f517", "Plugin-free Tally spine", "Reads TallyPrime over its own HTTP server. Live day-book sync every few minutes. FIFO allocation, never double-counts."),
        ("\U0001f4ac", "Collections engine", "Bills and reminders from the shop's own number. One consolidated reminder per party. Tone and language profiles."),
        ("\U0001f6e1️", "Sending safety", "Runs from the shop's own line. Daily caps, human pacing, quiet hours, number validation, opt-out honoured."),
        ("\U0001f916", "Owner assistant", "Run your books by texting: LIST, REMIND TOP 10, snap a paper bill for AI reading, PAID, TERMS, on a separate number."),
        ("\U0001f4ca", "End-of-day digest", "A nightly summary: sales, receipts, outstanding, and exactly who was reminded, by name, at a time you pick."),
        ("⚡", "5-min onboarding", "One installer, no keys. Type an 8-char code, pick the Tally company, scan WhatsApp. Self-updating."),
    ]
    cards = "".join(
        f'<div class="card"><div class="ico">{i}</div><h3>{h}</h3><p>{p}</p></div>'
        for i, h, p in cards_data)

    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Everything in the product</span>
  <h1>The whole surface, nothing hidden.</h1>
  <p class="lede">ASVA is built around one job, recovering your outstanding, and every capability serves it.
    Clear, automatic, and safe for your WhatsApp.</p>
  <div class="cta-row"><a class="btn btn-p" href="{DOWNLOAD_FILE}" download>Download for Windows</a>
    <a class="btn btn-s" href="{WA_TRY}">Talk to us on WhatsApp</a></div>
 </section>
 <section><div class="grid g3 reveal">{cards}</div></section>

 <section>
  <div class="sechead"><span class="eyebrow">Why ASVA wins</span>
   <h2>The difference the customer can see</h2>
   <p>The same collections job, done the way a distributor actually needs it.</p></div>
  {_compare_table()}
 </section>
</div>
{_band("Put these to work on your ledger",
       "Message us and we will set up bills, reminders and your first digest together.")}"""
    crumbs = _breadcrumb("Features", "/features")
    return page_shell(
        path="/features",
        title="ASVA features | Tally sync, WhatsApp reminders, owner assistant",
        description="ASVA features: Tally-native sync, WhatsApp bills and timed reminders from your own number, a WhatsApp owner assistant, UPI pay links, photo-bill capture, and a nightly digest.",
        keywords="WhatsApp billing features, Tally reminder software features, WhatsApp owner assistant, UPI payment link reminder, photo bill OCR, debtor management features India, ASVA vs CredFlow, ASVA vs Biz Analyst",
        body=body, jsonld=crumbs)


def _compare_table() -> str:
    rows = [
        ("Sends from the shop's own WhatsApp", "Yes", "Generic number", "Call and SMS", "Not built for it"),
        ("Flat price, no per-message coins", "Yes", "Per-message", "Tiered credits", "Add-on cost"),
        ("Runs the reminder cadence for you", "Yes", "Manual", "Partly", "No"),
        ("Reconciles receipts back to Tally", "Yes", "Read-only", "Separate ledger", "N/A"),
        ("Owner can run it by WhatsApp", "Yes", "App only", "App only", "No"),
        ("Never holds or routes the money", "Yes", "Yes", "Takes a cut", "Yes"),
    ]
    trs = ""
    for cap, us, a, b, c in rows:
        trs += (f'<tr><td style="font-weight:600">{cap}</td>'
                f'<td class="yes">{us}</td><td class="c">{a}</td>'
                f'<td class="c">{b}</td><td class="c">{c}</td></tr>')
    return f"""<div class="compare reveal"><table>
  <thead><tr><th>Capability</th><th class="c us">ASVA</th><th class="c">Typical add-on</th>
    <th class="c">Receivables app</th><th class="c">Tally alone</th></tr></thead>
  <tbody>{trs}</tbody></table>
  <div class="ftnote">Comparison reflects the common approach of each category based on publicly available
    information. Product features change over time.</div>
</div>"""


FAQ = [
    ("Is ASVA a mobile app or a desktop app?", "ASVA is a Windows desktop app. You install it once on the same computer where you run TallyPrime. There is no mobile app to install, and your customers install nothing. They simply receive a normal WhatsApp message from your own number."),
    ("Which Windows do I need, and what else?", "A Windows 10 or Windows 11 computer with TallyPrime, and your WhatsApp. Download one installer, type the short setup code we read out to you, pick your company and scan WhatsApp. About five minutes, and there is nothing else to install."),
    ("Do my customers need to install anything?", "No. Your customers just get a normal WhatsApp message from your own number. Nothing for them to install, download or learn."),
    ("How do I pay for ASVA?", "Directly by UPI each month. We share a pay link near your renewal date, and once the payment is received your cycle continues automatically."),
    ("Is there a setup fee?", "No. Onboarding is free, we set up your first batch with you, and you can cancel anytime."),
    ("What counts as an active debtor?", "A customer with an outstanding balance that ASVA can chase. Your plan is sized to that count, not to how many messages you send."),
    ("Does ASVA change my Tally data?", "Never. ASVA reads your ledger and confirms payments back from Tally. It does not post or edit vouchers."),
    ("Do messages come from my number or ASVA's?", "Your own WhatsApp number, so customers recognise your shop and trust the message."),
    ("Will it spam my customers?", "No. Reminders follow each party's credit terms, respect a daily cap, and stop as soon as a customer replies."),
    ("Does ASVA hold or route my money?", "No. Every reminder carries a UPI link straight to your own account. ASVA never touches the payment and takes no cut."),
    ("Is it hard to set up?", "No. There are no files to edit and no settings to figure out. If you can link WhatsApp Web, you can set up ASVA, and we stay on the phone with you while you do it."),
    ("Can I try it first?", "Yes. Message us and we will connect your Tally and set up your first batch with you."),
]


def _pricing() -> str:
    faq = "".join(
        f"<details class='reveal'><summary>{q}</summary><p>{a}</p></details>"
        for q, a in FAQ)
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Free pilot</span>
  <h1>Free for every shop, <span class="hl">till 15 September</span></h1>
  <p class="lede">We are running an open pilot. Every shop that joins now uses the full ASVA,
    bills, reminders, the daily digest and the WhatsApp assistant, completely free until
    15 September 2026. No card, no setup fee. Get your stuck money back first, decide later.</p>
  <div style="margin-top:26px;display:flex;gap:12px;flex-wrap:wrap">
    <a class="btn btn-p" href="/download">Download and start free</a>
    <a class="btn" href="{WA_TRY}">Talk to us on WhatsApp</a>
  </div>
  <p class="undernote" style="margin-top:18px">No card &middot; no setup fee &middot; your data stays yours</p>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">FAQ</span><h2>Questions, answered</h2></div>
  <div class="faq reveal">{faq}</div>
 </section>
</div>
{_band("Want ASVA set up for your shop?",
       "Message us on WhatsApp and we will get you live in about five minutes, free till 15 September.")}"""
    faq_ld = json.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a}}
                       for q, a in FAQ],
    })
    return page_shell(
        path="/pricing",
        title="ASVA is free till 15 September | open pilot for Tally shops",
        description="ASVA is free for every shop until 15 September 2026. Full product, no card, no setup fee. Automatic WhatsApp bills and payment reminders from your TallyPrime.",
        keywords="ASVA free trial, free Tally WhatsApp reminder, free payment reminder software India, receivables software free pilot",
        body=body, jsonld=faq_ld)


def _use_cases() -> str:
    industries = [
        ("Electrical &amp; lighting", "Long ledgers of small-ticket retailers. ASVA keeps every one on a steady reminder cadence."),
        ("Hardware &amp; tools", "High volume, many walk-in credit accounts. Bills and reminders go out without manual follow-up."),
        ("Chemicals", "Fewer, larger accounts with strict credit terms. ASVA times reminders to each party's terms."),
        ("Steel &amp; metals", "Big-value bills where days saved on collection matter most. Faster reminders, faster cash."),
        ("Paints", "Seasonal demand and dealer credit. ASVA holds the follow-up so nothing slips in the rush."),
        ("Pipes &amp; fittings", "Project-based buyers on extended credit. Timed reminders keep those cycles moving."),
    ]
    situations = [
        ("Too many small debtors to chase", "When you have hundreds of accounts, manual follow-up breaks down. ASVA reminds every one, on time, from your number."),
        ("Long credit cycles", "Set reminders to each party's terms so accounts get nudged exactly when they should, not too early, not too late."),
        ("Festival and season-end collections", "Push a clean reminder run before a season closes, without a WhatsApp ban and without a call marathon."),
        ("Multiple companies in one Tally", "Run collections across companies from one place, each with its own bills, reminders and digest."),
    ]
    icards = "".join(
        f'<div class="card"><h3><span class="dot"></span>{h}</h3><p>{p}</p></div>'
        for h, p in industries)
    scards = "".join(
        f'<div class="card"><h3><span class="dot"></span>{h}</h3><p>{p}</p></div>'
        for h, p in situations)
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Use cases</span>
  <h1>Made for distributors who sell on credit</h1>
  <p class="lede">If your money is stuck in a long list of debtors, ASVA is built for you.
    Here is where it fits best.</p>
  <div class="cta-row"><a class="btn btn-p" href="{DOWNLOAD_FILE}" download>Download for Windows</a>
    <a class="btn btn-s" href="/how-it-works">See how it works</a></div>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">By trade</span><h2>Industries we serve</h2></div>
  <div class="grid g3 reveal">{icards}</div>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">By situation</span><h2>When ASVA pays for itself</h2></div>
  <div class="grid g2 reveal">{scards}</div>
 </section>
</div>
{_band("See it on your own ledger",
       "Message us with your trade and debtor count, and we will show you exactly how ASVA would run.")}"""
    crumbs = _breadcrumb("Use cases", "/use-cases")
    return page_shell(
        path="/use-cases",
        title="ASVA use cases | WhatsApp collections for Tally distributors",
        description="Where ASVA fits: electrical, hardware, chemical, steel, paint and pipe distributors on TallyPrime, and situations like many small debtors, long credit cycles and season-end collections.",
        keywords="collection software for distributors, electrical distributor billing, hardware wholesale receivables, chemical distributor payment reminder, steel trader collections, WhatsApp reminder for wholesalers",
        body=body, jsonld=crumbs)


def _breadcrumb(name: str, path: str) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": _base() + "/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": _base() + path},
        ],
    })


def _download() -> str:
    """A real download landing page on the marketing domain (SEO + no 404). The
    actual installer file is served by the i3 app (downloads.py); this page's CTA
    sends people there. On the app domain, downloads.py owns /download instead."""
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="badge"><span class="d"></span> Windows 10 or 11 &middot; version {DOWNLOAD_VERSION}</span>
  <h1 style="margin-top:16px">Download ASVA <span class="hl">for Windows.</span></h1>
  <p class="lede"><strong>Free for every shop till 15 September 2026.</strong> One installer for the computer
    where you run TallyPrime. It reads your Tally and sends bills and payment reminders on WhatsApp from your
    own number. You need Windows 10 or 11, TallyPrime, and the short setup code we give you. Nothing else to install.</p>
  <div class="cta-row">
    <a class="btn btn-p" href="{DOWNLOAD_FILE}" download>Download ASVA {DOWNLOAD_VERSION}</a>
    <a class="btn btn-s" href="{WA_TRY}">Talk to us on WhatsApp</a>
  </div>
  <div class="undernote">You are downloading ASVA version {DOWNLOAD_VERSION}. Already have ASVA? Running this installs the update on top of it. No setup code yet? Message us.</div>
 </section>
 <section>
  <div class="sechead"><span class="eyebrow">Setup</span><h2>Three steps, about five minutes</h2></div>
  {_simple_steps()}
 </section>
 <section>
  <div class="sechead"><span class="eyebrow">What you need</span><h2>That is the whole list</h2></div>
  {_requirements()}
 </section>
 <section>
  <div class="sechead"><span class="eyebrow">What's new</span><h2>Recent updates</h2>
   <p>What each recent version added, newest first.</p></div>
  {_whats_next()}
 </section>
</div>
{_band("Need a hand setting up?",
       "Message us and we will install ASVA with you and send your first batch of reminders together.")}"""
    crumbs = _breadcrumb("Download", "/download")
    return page_shell(
        path="/download",
        title="Download ASVA for Windows | Tally to WhatsApp collections app",
        description="Download ASVA, the small Windows app that runs next to TallyPrime and sends your bills and payment reminders on WhatsApp from your own number. Windows 10 or 11, setup in about five minutes.",
        keywords="download ASVA, ASVA for Windows, Tally WhatsApp app download, TallyPrime WhatsApp reminder app download, payment reminder software India download",
        body=body, jsonld=crumbs)


def _whats_next() -> str:
    """What's new: the last few shipped versions (newest first), then a short,
    honestly-labelled 'coming next'. Momentum without over-promising."""
    shipped = ""
    for i, (ver, title, desc) in enumerate(VERSIONS):
        label = f"Latest &middot; v{ver}" if i == 0 else f"Shipped &middot; v{ver}"
        cls = " live" if i == 0 else ""
        shipped += f'<div class="card"><span class="soon{cls}">{label}</span><h3>{title}</h3><p>{desc}</p></div>'
    coming = "".join(
        f'<div class="card"><span class="soon">Coming next</span><h3>{h}</h3><p>{p}</p></div>'
        for h, p in COMING)
    return (f'<div class="grid g3 reveal">{shipped}</div>'
            f'<div class="grid g2 reveal" style="margin-top:16px">{coming}</div>')


PAGES = {
    "/": _home,
    "/how-it-works": _how,
    "/features": _features,
    "/pricing": _pricing,
    "/use-cases": _use_cases,
    "/download": _download,
}


def render(path: str) -> str:
    return PAGES.get(path, _home)()


def landing_html() -> str:
    """Backward-compatible: the home page (used by build_zip's static landing)."""
    return _home()


def sitemap_xml() -> str:
    base = _base()
    urls = "".join(
        f"<url><loc>{base}{p if p != '/' else '/'}</loc>"
        f"<changefreq>weekly</changefreq><priority>{'1.0' if p == '/' else '0.8'}</priority></url>"
        for p in PAGES)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")


# Answer-engine + search crawlers we explicitly welcome. Listing them (all with
# "Allow: /") makes intent unambiguous so AI assistants and search engines can
# read, index and cite the site. "*" already permits everyone; the named blocks
# are a clear signal, not a restriction.
_AI_AGENTS = [
    "Googlebot", "Bingbot", "DuckDuckBot", "Applebot", "Applebot-Extended",
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
    "anthropic-ai", "Claude-SearchBot", "PerplexityBot", "Perplexity-User",
    "Google-Extended", "CCBot", "Amazonbot", "Bytespider", "Meta-ExternalAgent",
    "cohere-ai", "YouBot", "Diffbot", "Timpibot", "Gemini",
]


# Marketing path -> static-site filename (Vercel/Pages clean URLs serve these).
_STATIC_FILES = {
    "/": "index.html", "/how-it-works": "how-it-works.html",
    "/features": "features.html", "/pricing": "pricing.html",
    "/use-cases": "use-cases.html", "/download": "download.html",
}


def _copy_og_image(dest_dir: str) -> None:
    """Best-effort: ship an og.png so social/AI cards have an image. Uses the
    repo's brand asset when the build runs from the repo (dev machine); harmless
    if it is missing (the meta tag just points at a 404, no page breaks)."""
    for cand in ("app/static/og.png", "pdf/og_logo.png", "og.png"):
        if os.path.exists(cand):
            try:
                shutil.copyfile(cand, os.path.join(dest_dir, "og.png"))
                return
            except Exception:
                pass


def export_static(dest_dir: str, *, base: str = "https://tryasva.com",
                  app_base: str = "https://app.tryasva.com") -> list[str]:
    """Render the whole marketing site to static files for a free host (Cloudflare
    Pages / Vercel / Netlify). Canonical + sitemap use `base` (the website domain);
    the Download link points at `app_base` (the i3 app), which serves the file."""
    global _BASE_OVERRIDE
    os.makedirs(dest_dir, exist_ok=True)
    written: list[str] = []
    _BASE_OVERRIDE = base
    try:
        for path, fn in _STATIC_FILES.items():
            # /download is now a real static page; its own CTA points at the app
            # domain (APP_BASE) for the file, so no link rewriting is needed.
            html = render(path)
            with open(os.path.join(dest_dir, fn), "w", encoding="utf-8") as f:
                f.write(html)
            written.append(fn)
        urls = "".join(
            f"<url><loc>{base}{p if p != '/' else '/'}</loc>"
            f"<changefreq>weekly</changefreq><priority>{'1.0' if p == '/' else '0.8'}</priority></url>"
            for p in _STATIC_FILES)
        sitemap = ('<?xml version="1.0" encoding="UTF-8"?>'
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   f"{urls}</urlset>")
        robots = (f"# ASVA - {TAGLINE}\n"
                  f"# Search engines and AI assistants are welcome to read and cite this site.\n\n"
                  f"User-agent: *\nAllow: /\n\n"
                  + "\n\n".join(f"User-agent: {a}\nAllow: /" for a in _AI_AGENTS)
                  + f"\n\nSitemap: {base}/sitemap.xml\n")
        llms = llms_txt()   # /download is a real page on the marketing domain now
        extra = {
            "sitemap.xml": sitemap,
            "robots.txt": robots,
            "llms.txt": llms,
            "vercel.json": json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=2),
        }
        for fn, content in extra.items():
            with open(os.path.join(dest_dir, fn), "w", encoding="utf-8") as f:
                f.write(content)
            written.append(fn)
        _copy_og_image(dest_dir)
        if os.path.exists(os.path.join(dest_dir, "og.png")):
            written.append("og.png")
        return written
    finally:
        _BASE_OVERRIDE = None


def robots_txt() -> str:
    blocks = "\n\n".join(f"User-agent: {a}\nAllow: /" for a in _AI_AGENTS)
    return (f"# ASVA - {TAGLINE}\n"
            f"# Search engines and AI assistants are welcome to read and cite this site.\n\n"
            f"User-agent: *\nAllow: /\n\n"
            f"{blocks}\n\n"
            f"Sitemap: {_base()}/sitemap.xml\n")


def llms_txt() -> str:
    """/llms.txt - a concise, crawl-friendly brief for AI answer engines, so an
    assistant asked 'how do I send Tally invoices on WhatsApp' can describe and
    cite ASVA accurately. Markdown, per the emerging llms.txt convention."""
    b = _base()
    return f"""# ASVA

> ASVA is a Windows desktop app for Indian distributors that connects to
> TallyPrime and automatically sends bills and payment reminders on WhatsApp
> from the shop's own number, so they collect outstanding payments faster
> without chasing. Tagline: {TAGLINE}

## What kind of app it is
ASVA is a Windows desktop application (Windows 10 or 11) that you install on the same computer where you run TallyPrime. It is not a mobile app and not a plugin inside Tally. Customers do not install anything; they receive normal WhatsApp messages from the shop's own number. Setup takes about five minutes: run one installer, type a short pairing code, pick the Tally company, and scan WhatsApp.

## What ASVA does
- Reads sales, receipts and outstanding balances directly from TallyPrime (read-only, books never leave the PC).
- Sends new bills with the PDF and timed payment reminders on WhatsApp, from the owner's own number, each with a UPI pay link.
- Runs the reminder cadence automatically, scaled to each party's credit terms, and stops the moment a customer replies.
- Reconciles payments from Tally automatically (oldest bills first) and sends the owner a nightly WhatsApp digest.
- Growth plan and above include a WhatsApp owner assistant: list debtors, check a party's balance, snap a paper bill for AI reading, or send a reminder by chat.

## How ASVA is different
- Sends from the shop's own WhatsApp number, not a generic brand number customers do not trust.
- Flat price by active debtors, with no per-message coins or credits.
- Never holds or routes the money: every reminder carries a UPI link straight to the shop's own account, and ASVA takes no cut.
- Plugin-free: it reads TallyPrime over its own HTTP server, so nothing is installed inside Tally.

## Who it is for
Distributors and wholesalers selling on credit in India: electrical, hardware, chemical, steel, paint, and pipe trades running TallyPrime on Windows, typically carrying 30 to 160 days of credit.

## Pricing
ASVA is in an open pilot and is FREE for every shop until 15 September 2026. Full
product, no card, no setup fee. Paid plans will be introduced after the pilot.

## Key pages
- Home: {b}/
- How it works: {b}/how-it-works
- Features: {b}/features
- Pricing: {b}/pricing
- Use cases: {b}/use-cases
- Download (Windows): {b}/download

## Contact
- WhatsApp: https://wa.me/{CONTACT_WA}
- Email: {CONTACT_EMAIL}
"""


# ── routes ──────────────────────────────────────────────────────────────────
def _serve(path: str):
    """Serve a marketing page, or (on the i3 app, SERVE_MARKETING=false) redirect
    to the static website so the app domain is not a duplicate of it."""
    if settings.serve_marketing:
        return HTMLResponse(render(path))
    target = (settings.marketing_url or "https://tryasva.com").rstrip("/") + (path if path != "/" else "/")
    return RedirectResponse(target, status_code=307)


@router.get("/", response_class=HTMLResponse)
def home_page():
    return _serve("/")


@router.get("/how-it-works", response_class=HTMLResponse)
def how_page():
    return _serve("/how-it-works")


@router.get("/features", response_class=HTMLResponse)
def features_page():
    return _serve("/features")


@router.get("/pricing", response_class=HTMLResponse)
def pricing_page():
    return _serve("/pricing")


@router.get("/use-cases", response_class=HTMLResponse)
def use_cases_page():
    return _serve("/use-cases")


@router.get("/sitemap.xml")
def sitemap():
    return Response(sitemap_xml(), media_type="application/xml")


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    # The app domain must not be indexed - SEO lives on the static website.
    if not settings.serve_marketing:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(robots_txt())


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms():
    return PlainTextResponse(llms_txt())
