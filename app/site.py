"""ASVA public marketing site - multi-page, SEO-first, server-rendered.

One shared shell (design system + sticky nav + footer + scroll reveal) wraps
every page. Pages: / , /how-it-works , /features , /pricing , /use-cases . Plus
/sitemap.xml and /robots.txt. Self-contained (inline CSS/JS, system fonts, no
external requests) so it loads instantly through the Cloudflare tunnel.

House style: English, clear and to the point, NO em/en dashes. Utilitarian
ledger aesthetic - near-white canvas, charcoal ink, one deep-green accent, a
monospace utility face for labels and figures. Edit CONTACT_WA / CONTACT_EMAIL.
"""
from __future__ import annotations

import json
import os
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

# SEO keyword bank. Intent-led terms an Indian distributor (or an AI answering
# for one) would actually search: Tally + WhatsApp + collections/receivables.
KEYWORDS_DEFAULT = (
    "ASVA, Tally WhatsApp reminder, TallyPrime payment reminder, WhatsApp billing "
    "software India, automatic payment reminder app, accounts receivable automation "
    "India, debtor follow up software, outstanding collection software, send Tally "
    "invoice on WhatsApp, payment reminder for distributors, credit collection "
    "software, Tally add on WhatsApp, receivables management India, UPI payment "
    "reminder, wholesale billing WhatsApp"
)


def _base() -> str:
    if _BASE_OVERRIDE:
        return _BASE_OVERRIDE.rstrip("/")
    return (settings.public_base_url or "https://tryasva.com").rstrip("/")


def _wa(text: str) -> str:
    return f"https://wa.me/{CONTACT_WA}?text={quote(text)}"


WA_TRY = _wa("I want to try ASVA")

# ── design system ───────────────────────────────────────────────────────────
CSS = """
:root{
 --bg:#fbfbfa;--card:#fff;--ink:#14201a;--muted:#667069;
 --line:#e7e5de;--hair:#eeece6;
 --accent:#0b6e37;--accent-d:#0e8a45;--wash:#ecf3ee;
 --amber-bg:#fbf3db;
 --mono:'SF Mono','JetBrains Mono',Consolas,ui-monospace,monospace;
 --sans:'SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
 --maxw:1080px;
}
*{box-sizing:border-box}
html{color-scheme:light;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 24px}
a{color:inherit;text-decoration:none}
.mono{font-family:var(--mono)}
.eyebrow{font-family:var(--mono);font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
img{max-width:100%}

header.nav{position:sticky;top:0;z-index:20;background:rgba(251,251,250,.85);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.nav .row{display:flex;align-items:center;justify-content:space-between;height:60px}
.logo{font-weight:800;letter-spacing:.16em;font-size:1rem}.logo b{color:var(--accent)}
.navlinks{display:flex;align-items:center;gap:22px}
.navlinks a{color:var(--muted);font-size:.9rem}
.navlinks a:hover,.navlinks a.on{color:var(--ink)}
.navlinks a.on{font-weight:600}
.navcta{background:var(--accent);color:#fff!important;padding:8px 16px;border-radius:8px;font-weight:600;font-size:.88rem}
.navcta:hover{background:var(--accent-d)}
@media(max-width:820px){.navlinks a.hidem{display:none}}

.btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.95rem;padding:12px 22px;border-radius:9px;border:1px solid transparent;transition:transform .12s,background .15s,border-color .15s}
.btn:active{transform:scale(.985)}
.btn-p{background:var(--accent);color:#fff}.btn-p:hover{background:var(--accent-d)}
.btn-s{background:#fff;color:var(--ink);border-color:var(--line)}.btn-s:hover{border-color:#cfccc1}
.cta-row{display:flex;gap:12px;flex-wrap:wrap}

h1{font-weight:800;letter-spacing:-.035em;line-height:1.04;margin:0 0 20px;text-wrap:balance}
h2{font-weight:800;letter-spacing:-.025em;margin:0 0 10px;text-wrap:balance}
.page-hero{padding:74px 0 18px;max-width:820px}
.page-hero .eyebrow{display:block;margin-bottom:18px}
.page-hero h1{font-size:clamp(2.3rem,6vw,3.9rem)}
.lede{font-size:1.18rem;color:var(--muted);max-width:640px;margin:0 0 28px}
.undernote{font-family:var(--mono);color:var(--muted);font-size:.85rem;margin-top:18px}
section{padding:44px 0}
.sechead{max-width:660px;margin-bottom:30px}
.sechead .eyebrow{display:block;margin-bottom:12px}
.sechead h2{font-size:clamp(1.6rem,3.4vw,2.3rem)}
.sechead p{color:var(--muted);margin:0}
.muted{color:var(--muted)}
.morelink{display:inline-block;margin-top:20px;font-weight:600;color:var(--accent)}

.grid{display:grid;gap:16px}
.g3{grid-template-columns:repeat(3,1fr)}.g2{grid-template-columns:repeat(2,1fr)}.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:820px){.g3,.g4{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.g2,.g3,.g4{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
.card h3{margin:0 0 7px;font-size:1.1rem;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
.card h3 .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
.card p{margin:0;color:var(--muted);font-size:.95rem}
.knum{font-family:var(--mono);font-size:.76rem;font-weight:700;color:var(--accent);letter-spacing:.1em;border-bottom:1px solid var(--hair);padding-bottom:12px;margin-bottom:14px}

.strip{border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:38px 0;padding:16px 0;font-family:var(--mono);font-size:.86rem;color:var(--muted)}
.strip b{color:var(--ink);font-weight:600}

.plan{display:flex;flex-direction:column}
.plan.best{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.tag{font-family:var(--mono);font-size:.66rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);height:14px;margin-bottom:8px}
.plan .name{font-weight:700;font-size:1.04rem}
.amt{font-family:var(--mono);font-size:1.9rem;font-weight:700;letter-spacing:-.02em;margin:10px 0 2px;font-variant-numeric:tabular-nums}
.amt.sm{font-size:1.35rem;margin-top:16px}
.amt .per{font-size:.82rem;color:var(--muted);font-weight:500}
.cap{font-family:var(--mono);font-size:.82rem;color:var(--ink);margin:2px 0 14px}
.plan ul{list-style:none;margin:0 0 18px;padding:0;flex:1}
.plan li{font-size:.9rem;color:var(--muted);padding:6px 0 6px 22px;position:relative}
.plan li::before{content:"";position:absolute;left:2px;top:12px;width:9px;height:9px;border-radius:2px;background:var(--wash);border:1px solid var(--accent)}
.plan li.no{color:#9aa39d}.plan li.no::before{background:var(--amber-bg);border-color:#e2c46a}
.buy{margin-top:auto;text-align:center;font-weight:600;font-size:.9rem;padding:10px;border-radius:8px;border:1px solid var(--line);color:var(--ink)}
.plan.best .buy{background:var(--accent);color:#fff;border-color:var(--accent)}
.buy:hover{border-color:#cfccc1}.plan.best .buy:hover{background:var(--accent-d)}

.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.split{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
.panel .h{font-family:var(--mono);font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
.panel ul{margin:0;padding-left:18px}.panel li{color:var(--muted);font-size:.94rem;padding:3px 0}

.flow{counter-reset:s;display:grid;gap:14px}
.flow .row{display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px}
.flow .row .idx{font-family:var(--mono);font-weight:700;color:var(--accent);font-size:.9rem;border:1px solid var(--line);border-radius:8px;width:38px;height:38px;display:flex;align-items:center;justify-content:center}
.flow .row h3{margin:2px 0 5px;font-size:1.12rem;letter-spacing:-.01em}
.flow .row p{margin:0;color:var(--muted);font-size:.95rem}

.faq{border-top:1px solid var(--line)}
.faq details{border-bottom:1px solid var(--line);padding:16px 2px}
.faq summary{cursor:pointer;font-weight:600;list-style:none;display:flex;justify-content:space-between;gap:12px}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";color:var(--accent);font-family:var(--mono)}
.faq details[open] summary::after{content:"-"}
.faq details p{margin:12px 0 0;color:var(--muted);font-size:.95rem}

.band{background:var(--ink);color:#fff;border-radius:18px;padding:52px 34px;text-align:center;margin:24px 0 56px}
.band .eyebrow{color:#5fd08a;display:block;margin-bottom:12px}
.band h2{color:#fff}.band p{color:#b8c6bd;max-width:520px;margin:0 auto 24px}

footer.ft{border-top:1px solid var(--line);padding:40px 0 56px}
.ft .cols{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap}
.ft .col .h{font-family:var(--mono);font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;color:var(--ink);margin-bottom:8px}
.ft .col a{display:block;color:var(--muted);font-size:.9rem;padding:4px 0}
.ft .col a:hover{color:var(--ink)}
.ft .brand{max-width:260px}.ft .brand .logo{font-size:1.05rem;margin-bottom:8px}
.ft .brand p{color:var(--muted);font-size:.9rem;margin:0}
.ft .base{margin-top:28px;padding-top:18px;border-top:1px solid var(--line);font-family:var(--mono);font-size:.8rem;color:var(--muted);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}

.reveal{opacity:0;transform:translateY(12px);transition:opacity .6s cubic-bezier(.16,1,.3,1),transform .6s cubic-bezier(.16,1,.3,1)}
.reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}html{scroll-behavior:auto}}
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
       ("/features", "Features"), ("/pricing", "Pricing"),
       ("/use-cases", "Use cases")]


def _nav(active: str) -> str:
    links = "".join(
        f'<a class="hidem{" on" if p == active else ""}" href="{p}">{label}</a>'
        for p, label in NAV)
    return f"""<header class="nav"><div class="wrap"><div class="row">
  <a class="logo" href="/">AS<b>V</b>A</a>
  <nav class="navlinks">{links}
    <a class="hidem" href="/download">Download</a>
    <a class="navcta" href="{WA_TRY}">Talk to us</a>
  </nav></div></div></header>"""


def _footer() -> str:
    return f"""<footer class="ft"><div class="wrap"><div class="cols">
  <div class="brand"><div class="logo">AS<b>V</b>A</div>
    <p>The recovery agent for Indian distributors on TallyPrime. {TAGLINE}</p></div>
  <div class="col"><div class="h">Product</div>
    <a href="/how-it-works">How it works</a><a href="/features">Features</a>
    <a href="/pricing">Pricing</a><a href="/use-cases">Use cases</a></div>
  <div class="col"><div class="h">Get started</div>
    <a href="/download">Download</a><a href="{WA_TRY}">Talk to us on WhatsApp</a>
    <a href="mailto:{CONTACT_EMAIL}">Email us</a></div>
</div>
<div class="base"><span>&copy; 2026 {SITE_NAME}. {TAGLINE}</span>
  <span><a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></span></div>
</div></footer>"""


def page_shell(*, path: str, title: str, description: str, body: str,
               jsonld: str = "", keywords: str = "") -> str:
    canonical = _base() + (path if path != "/" else "/")
    ld = (f'<script type="application/ld+json">{jsonld}</script>' if jsonld else "")
    kw = f'<meta name="keywords" content="{keywords or KEYWORDS_DEFAULT}">'
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{title}</title>
<meta name="description" content="{description}">
{kw}
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta name="author" content="{SITE_NAME}">
<link rel="canonical" href="{canonical}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<style>{CSS}</style>{ld}
</head><body>
{_nav(path)}
<main>{body}</main>
{_footer()}
{REVEAL_JS}
</body></html>"""


# ── reusable content blocks ─────────────────────────────────────────────────
def _steps_grid() -> str:
    steps = [
        ("STEP 01", "Connect Tally",
         "A small app sits next to TallyPrime and reads your sales and receipts. No exports, no spreadsheets, no cloud upload of your books."),
        ("STEP 02", "ASVA sends on WhatsApp",
         "New bills go out with the PDF. Overdue accounts get polite, timed reminders with a UPI pay link, all from your own number."),
        ("STEP 03", "You get paid",
         "Payments reconcile from Tally automatically. Each evening you get a WhatsApp digest of new bills, money collected, and who to call."),
    ]
    cards = "".join(
        f'<div class="card"><div class="knum">{n}</div><h3>{h}</h3><p>{p}</p></div>'
        for n, h, p in steps)
    return f'<div class="grid g3 reveal">{cards}</div>'


def _features_grid() -> str:
    feats = [
        ("From your own number", "Customers hear from your shop, not an unknown brand. Trust and relationships stay intact."),
        ("Timed, never spammy", "Reminders follow each party's credit terms and back off the moment they reply. No blasting, no bans."),
        ("Tally stays the truth", "ASVA never edits your books. It reads what is outstanding and confirms payments back from Tally."),
        ("End-of-day digest", "One clean WhatsApp summary each night: new bills, collections, and the accounts worth a call."),
    ]
    cards = "".join(
        f'<div class="card"><h3><span class="dot"></span>{h}</h3><p>{p}</p></div>'
        for h, p in feats)
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


def _band(title: str, sub: str) -> str:
    return f"""<div class="wrap"><div class="band reveal">
  <span class="eyebrow">Get started</span><h2>{title}</h2><p>{sub}</p>
  <a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a></div></div>"""


# ── pages ───────────────────────────────────────────────────────────────────
def _home() -> str:
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">TallyPrime &middot; WhatsApp &middot; Auto-collections</span>
  <h1>Your outstanding,<br><span style="color:var(--accent)">collected on its own.</span></h1>
  <p class="lede">ASVA reads your TallyPrime ledger and sends every bill and payment reminder
    on WhatsApp, from your own number. You stop chasing customers, and the money comes in sooner.</p>
  <div class="cta-row">
    <a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a>
    <a class="btn btn-s" href="/how-it-works">See how it works</a>
  </div>
  <div class="undernote">Set up in ~5 minutes &middot; uses your existing WhatsApp &middot; your books never leave your PC</div>
 </section>

 <div class="strip reveal">Built for <b>electrical, hardware, chemical, steel, paint and pipe</b> distributors running <b>TallyPrime</b>.</div>

 <section>
  <div class="sechead"><span class="eyebrow">How it works</span>
   <h2>Three steps, then it runs itself</h2>
   <p>Your ledger stays the source of truth. ASVA reads it and does the chasing for you.</p></div>
  {_steps_grid()}
  <a class="morelink" href="/how-it-works">See the full walkthrough &rarr;</a>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">Why ASVA</span>
   <h2>A recovery agent, not another reminder app</h2>
   <p>One job: getting your outstanding paid, without annoying your customers.</p></div>
  {_features_grid()}
  <a class="morelink" href="/features">Explore all features &rarr;</a>
 </section>

 <section>
  <div class="sechead"><span class="eyebrow">Pricing</span>
   <h2>Priced by active debtors</h2>
   <p>You pay for the customers ASVA chases, not per message. Simple, monthly, direct.</p></div>
  {_pricing_grid()}
  <a class="morelink" href="/pricing">Full pricing and FAQ &rarr;</a>
 </section>
</div>
{_band("See your first reminders go out today",
       "Send us a message and we will connect your Tally and set up your first batch together.")}"""
    graph = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "@id": _base() + "/#org", "name": SITE_NAME,
         "url": _base(), "email": CONTACT_EMAIL, "slogan": TAGLINE,
         "description": "Automatic WhatsApp bills and payment reminders from TallyPrime for Indian distributors."},
        {"@type": "WebSite", "@id": _base() + "/#website", "url": _base(),
         "name": SITE_NAME, "publisher": {"@id": _base() + "/#org"},
         "inLanguage": "en-IN"},
        {"@type": "SoftwareApplication", "name": "ASVA",
         "applicationCategory": "BusinessApplication",
         "operatingSystem": "Windows 10, Windows 11",
         "description": "ASVA connects to TallyPrime and automatically sends bills and payment reminders on WhatsApp from your own number, for Indian distributors selling on credit.",
         "offers": {"@type": "Offer", "price": "699", "priceCurrency": "INR"},
         "featureList": ["Tally sync", "WhatsApp bills and reminders",
                         "WhatsApp owner assistant", "UPI pay links",
                         "End-of-day digest", "Photo-bill capture"]},
    ]})
    return page_shell(
        path="/",
        title="ASVA - Collect faster. Stop chasing. | WhatsApp collections for Tally",
        description="ASVA reads your TallyPrime ledger and sends every bill and payment reminder on WhatsApp, from your own number. Automatic collections for Indian distributors. From Rs 699/mo.",
        keywords="Tally WhatsApp reminder, WhatsApp billing software India, automatic payment reminder, accounts receivable automation India, TallyPrime add on, collect outstanding payments, ASVA",
        body=body, jsonld=graph)


def _how() -> str:
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
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">How it works</span>
  <h1>How ASVA works</h1>
  <p class="lede">From connecting Tally to getting paid, here is exactly what happens, and what runs where.</p>
  <div class="cta-row"><a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a>
    <a class="btn btn-s" href="/features">See the features</a></div>
 </section>

 <section><div class="flow reveal">{flow}</div></section>

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
    return page_shell(
        path="/how-it-works",
        title="How ASVA works | Tally to WhatsApp collections, step by step",
        description="How ASVA works: connect TallyPrime, send bills and timed reminders on WhatsApp from your own number, reconcile payments from Tally, and stay in control with a nightly digest.",
        keywords="how to send Tally invoice on WhatsApp, Tally WhatsApp integration, automatic payment reminder Tally, reconcile Tally payments, WhatsApp reminder workflow",
        body=body)


def _features() -> str:
    groups = [
        ("Tally-native sync", "Reads sales, receipts and outstanding straight from TallyPrime. Matches payments oldest-first, carries opening balances, and handles multiple companies. Read-only, never edits your books."),
        ("WhatsApp from your own number", "Bills go out with the PDF and reminders follow, all from your existing WhatsApp. Customers need no new app and recognise your shop instantly."),
        ("Timed reminder engine", "Uses each party's credit terms to decide who is overdue, respects a daily cap, spaces sends like a human, and backs off the moment a customer replies."),
        ("WhatsApp owner assistant", "On Growth and above, run your recovery by chat: list debtors, check a party's balance, or send a reminder on demand. Answers come back on WhatsApp."),
        ("End-of-day digest", "A clean nightly summary: new bills raised, money collected, and the accounts worth a call, so you always know where you stand."),
        ("UPI pay links", "Every reminder carries a tap-to-pay UPI link, so the customer can clear the bill in a couple of taps."),
        ("Photo-bill capture", "Snap a photo of a paper bill and ASVA reads it and records it, so cash-counter sales are covered too."),
        ("Safe and reliable", "Server-side send limits protect your WhatsApp, every message is logged for audit, and your subscription is handled for you."),
    ]
    cards = "".join(
        f'<div class="card"><h3><span class="dot"></span>{h}</h3><p>{p}</p></div>'
        for h, p in groups)
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Features</span>
  <h1>Everything you need to get paid</h1>
  <p class="lede">ASVA is built around one job, recovering your outstanding, and every feature serves it.
    Clear, automatic, and safe for your WhatsApp.</p>
  <div class="cta-row"><a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a>
    <a class="btn btn-s" href="/pricing">See pricing</a></div>
 </section>
 <section><div class="grid g2 reveal">{cards}</div></section>
</div>
{_band("Put these to work on your ledger",
       "Message us and we will set up bills, reminders and your first digest together.")}"""
    return page_shell(
        path="/features",
        title="ASVA features | Tally sync, WhatsApp reminders, owner assistant",
        description="ASVA features: Tally-native sync, WhatsApp bills and timed reminders from your own number, a WhatsApp owner assistant, UPI pay links, photo-bill capture, and a nightly digest.",
        keywords="WhatsApp billing features, Tally reminder software features, WhatsApp owner assistant, UPI payment link reminder, photo bill OCR, debtor management features India",
        body=body)


FAQ = [
    ("How do I pay for ASVA?", "Directly by UPI each month. We share a pay link near your renewal date, and once the payment is received your cycle continues automatically."),
    ("Is there a setup fee?", "No. Onboarding is free, we set up your first batch with you, and you can cancel anytime."),
    ("What counts as an active debtor?", "A customer with an outstanding balance that ASVA can chase. Your plan is sized to that count, not to how many messages you send."),
    ("Does ASVA change my Tally data?", "Never. ASVA reads your ledger and confirms payments back from Tally. It does not post or edit vouchers."),
    ("Do messages come from my number or ASVA's?", "Your own WhatsApp number, so customers recognise your shop and trust the message."),
    ("Will it spam my customers?", "No. Reminders follow each party's credit terms, respect a daily cap, and stop as soon as a customer replies."),
    ("What do I need to run it?", "A Windows PC with TallyPrime and your WhatsApp. Download one installer, type the short setup code we read out to you, pick your company and scan WhatsApp. About five minutes, and there is nothing else to install."),
    ("Is it hard to set up?", "No. There are no files to edit and no settings to figure out. If you can link WhatsApp Web, you can set up ASVA, and we stay on the phone with you while you do it."),
    ("Can I try it first?", "Yes. Message us and we will connect your Tally and set up your first batch with you."),
]


def _pricing() -> str:
    faq = "".join(
        f"<details class='reveal'><summary>{q}</summary><p>{a}</p></details>"
        for q, a in FAQ)
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Pricing</span>
  <h1>Simple, priced by debtors</h1>
  <p class="lede">You pay for the customers ASVA actually chases, not per message. Monthly, direct by UPI,
    no setup fee, cancel anytime. Every plan sends bills, reminders and the digest.
    Growth and above add the WhatsApp owner assistant.</p>
 </section>
 <section>{_pricing_grid()}
   <p class="undernote">Pay directly by UPI &middot; no setup fee &middot; cancel anytime</p></section>

 <section>
  <div class="sechead"><span class="eyebrow">FAQ</span><h2>Questions, answered</h2></div>
  <div class="faq reveal">{faq}</div>
 </section>
</div>
{_band("Not sure which plan fits?",
       "Tell us your debtor count and we will point you to the right plan and set it up.")}"""
    offers = [{
        "@type": "Offer", "name": f"ASVA {p['name']}",
        "priceCurrency": "INR",
        "price": p["price"].replace("&#8377;", "").replace(",", "") if p["per"] else "0",
        "url": _base() + "/pricing",
    } for p in PLANS]
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "Product",
        "name": "ASVA", "brand": {"@type": "Brand", "name": "ASVA"},
        "description": "Automatic WhatsApp bills and payment reminders from TallyPrime.",
        "offers": offers,
    })
    faq_ld = json.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a}}
                       for q, a in FAQ],
    })
    return page_shell(
        path="/pricing",
        title="ASVA pricing | From Rs 699/mo, priced by active debtors",
        description="ASVA pricing: Basic Rs 699 (300 debtors), Growth Rs 1,099 (500 debtors, owner assistant), Pro Rs 1,999 (1,000 debtors), and Custom. Pay by UPI, no setup fee, cancel anytime.",
        keywords="ASVA price, Tally WhatsApp reminder price India, payment reminder software cost, WhatsApp billing software pricing, receivables software price India",
        body=body, jsonld=ld + "</script><script type=\"application/ld+json\">" + faq_ld)


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
  <div class="cta-row"><a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a>
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
    return page_shell(
        path="/use-cases",
        title="ASVA use cases | WhatsApp collections for Tally distributors",
        description="Where ASVA fits: electrical, hardware, chemical, steel, paint and pipe distributors on TallyPrime, and situations like many small debtors, long credit cycles and season-end collections.",
        keywords="collection software for distributors, electrical distributor billing, hardware wholesale receivables, chemical distributor payment reminder, steel trader collections, WhatsApp reminder for wholesalers",
        body=body)


PAGES = {
    "/": _home,
    "/how-it-works": _how,
    "/features": _features,
    "/pricing": _pricing,
    "/use-cases": _use_cases,
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
        for p in list(PAGES) + ["/download"])
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
    "cohere-ai", "YouBot", "Diffbot", "Timpibot",
]


# Marketing path -> static-site filename (Vercel/Pages clean URLs serve these).
_STATIC_FILES = {
    "/": "index.html", "/how-it-works": "how-it-works.html",
    "/features": "features.html", "/pricing": "pricing.html",
    "/use-cases": "use-cases.html",
}


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
            html = render(path).replace('href="/download"', f'href="{app_base}/download"')
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
        llms = llms_txt().replace(f"{base}/download", f"{app_base}/download")
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

> ASVA is software for Indian distributors that connects to TallyPrime and
> automatically sends bills and payment reminders on WhatsApp from the shop's
> own number, so they collect outstanding payments faster without chasing.

## What ASVA does
- Reads sales, receipts and outstanding balances directly from TallyPrime (read-only, books never leave the PC).
- Sends new bills with the PDF and timed payment reminders on WhatsApp, from the owner's own number, each with a UPI pay link.
- Reconciles payments from Tally automatically (oldest bills first) and sends the owner a nightly WhatsApp digest.
- Growth plan and above include a WhatsApp owner assistant: list debtors, check a party's balance, or send a reminder by chat.

## Who it is for
Distributors and wholesalers selling on credit in India: electrical, hardware, chemical, steel, paint, and pipe trades running TallyPrime on Windows.

## Pricing (INR per month, priced by active debtors)
- Basic: Rs 699, up to 300 debtors, bills and reminders and digest (no assistant).
- Growth: Rs 1,099, up to 500 debtors, adds the WhatsApp owner assistant.
- Pro: Rs 1,999, up to 1,000 debtors.
- Custom: for 1,000+ debtors and multiple companies.
Billing is direct by UPI, no setup fee, cancel anytime.

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
