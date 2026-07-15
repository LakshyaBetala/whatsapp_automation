"""The public ASVA landing page, served server-side at GET /.

Self-contained HTML (inline CSS, system fonts, no external requests) so it loads
instantly through the Cloudflare tunnel with nothing to fetch. Utilitarian
ledger aesthetic: near-white canvas, charcoal ink, one deep-green accent, a
monospace utility face for labels and prices. English, no em dashes (house
style). Edit CONTACT_WA / CONTACT_EMAIL before going live.
"""
from __future__ import annotations

CONTACT_WA = "919344110272"           # ASVA's own WhatsApp (bot/company number) - where prospects reach us. NOT a shop's number.
CONTACT_EMAIL = "almmatix@gmail.com"   # ASVA contact inbox

LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>ASVA - Collect faster. Stop chasing.</title>
<meta name="description" content="ASVA reads your TallyPrime ledger and sends every bill and payment reminder on WhatsApp, from your own number, so your distribution business gets paid sooner.">
<style>
  :root{
    --bg:#fcfcfb; --card:#ffffff; --ink:#17201b; --muted:#6c7872;
    --line:#e8e6df; --hair:#eceae4;
    --accent:#0b6e37; --accent-d:#0e8a45; --wash:#ecf3ee;
    --amber:#8a5a00; --amber-bg:#fbf3db;
    --mono:'SF Mono','JetBrains Mono',Consolas,'Roboto Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box}
  html{color-scheme:light}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:'SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
    line-height:1.6;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
  .wrap{max-width:1080px;margin:0 auto;padding:0 24px}
  a{color:inherit;text-decoration:none}
  .mono{font-family:var(--mono)}
  .eyebrow{font-family:var(--mono);font-size:.72rem;font-weight:600;letter-spacing:.14em;
    text-transform:uppercase;color:var(--accent)}
  .num{font-variant-numeric:tabular-nums}

  .btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.95rem;
    padding:12px 22px;border-radius:8px;border:1px solid transparent;
    transition:transform .12s ease,background .15s ease,border-color .15s ease}
  .btn:active{transform:scale(.985)}
  .btn-p{background:var(--accent);color:#fff}.btn-p:hover{background:var(--accent-d)}
  .btn-s{background:#fff;color:var(--ink);border-color:var(--line)}
  .btn-s:hover{border-color:#cfccc1}

  /* nav */
  nav{display:flex;align-items:center;justify-content:space-between;padding:22px 0}
  .logo{font-weight:800;letter-spacing:.16em;font-size:1.02rem}
  .logo b{color:var(--accent)}
  .links{display:flex;align-items:center;gap:26px}
  .links a{color:var(--muted);font-size:.9rem}
  .links a:hover{color:var(--ink)}
  .links .cta{color:var(--accent);font-weight:600}
  @media(max-width:680px){.links a:not(.cta){display:none}}

  /* hero */
  .hero{padding:70px 0 26px;max-width:800px}
  .hero .eyebrow{display:block;margin-bottom:20px}
  h1{font-size:clamp(2.35rem,6.4vw,4.1rem);line-height:1.03;letter-spacing:-.035em;
    font-weight:800;margin:0 0 22px;text-wrap:balance}
  h1 .g{color:var(--accent)}
  .lede{font-size:1.2rem;color:var(--muted);margin:0 0 30px;max-width:620px}
  .cta-row{display:flex;gap:12px;flex-wrap:wrap}
  .undernote{margin-top:18px;color:var(--muted);font-size:.86rem;font-family:var(--mono)}

  /* credibility strip */
  .strip{border-top:1px solid var(--line);border-bottom:1px solid var(--line);
    margin:46px 0;padding:16px 0;color:var(--muted);font-size:.86rem;
    font-family:var(--mono);letter-spacing:.01em}
  .strip b{color:var(--ink);font-weight:600}

  /* sections */
  section{padding:52px 0}
  .head{margin-bottom:34px;max-width:640px}
  .head .eyebrow{display:block;margin-bottom:12px}
  h2{font-size:clamp(1.7rem,3.6vw,2.4rem);letter-spacing:-.025em;font-weight:800;margin:0 0 10px;text-wrap:balance}
  .head p{color:var(--muted);margin:0}

  /* how - numbered steps */
  .steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  @media(max-width:760px){.steps{grid-template-columns:1fr}}
  .step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px}
  .step .n{font-family:var(--mono);font-size:.78rem;font-weight:700;color:var(--accent);
    letter-spacing:.1em;border-bottom:1px solid var(--hair);padding-bottom:12px;margin-bottom:14px}
  .step h3{margin:0 0 6px;font-size:1.14rem;letter-spacing:-.01em}
  .step p{margin:0;color:var(--muted);font-size:.95rem}

  /* why - bento */
  .feat{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
  @media(max-width:680px){.feat{grid-template-columns:1fr}}
  .f{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px 26px}
  .f h3{margin:0 0 7px;font-size:1.06rem;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
  .f h3 .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
  .f p{margin:0;color:var(--muted);font-size:.94rem}

  /* pricing */
  .price{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:stretch}
  @media(max-width:880px){.price{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:480px){.price{grid-template-columns:1fr}}
  .plan{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;
    display:flex;flex-direction:column}
  .plan.best{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
  .plan .tag{font-family:var(--mono);font-size:.66rem;font-weight:700;letter-spacing:.1em;
    text-transform:uppercase;color:var(--accent);margin-bottom:8px;height:14px}
  .plan .name{font-weight:700;font-size:1.04rem}
  .plan .amt{font-family:var(--mono);font-size:1.9rem;font-weight:700;letter-spacing:-.02em;margin:10px 0 2px;font-variant-numeric:tabular-nums}
  .plan .amt .per{font-size:.82rem;color:var(--muted);font-weight:500}
  .plan .cap{font-family:var(--mono);font-size:.82rem;color:var(--ink);margin:2px 0 14px}
  .plan ul{list-style:none;margin:0 0 18px;padding:0;flex:1}
  .plan li{font-size:.9rem;color:var(--muted);padding:6px 0 6px 22px;position:relative}
  .plan li::before{content:"";position:absolute;left:2px;top:12px;width:9px;height:9px;border-radius:2px;background:var(--wash);border:1px solid var(--accent)}
  .plan li.no{color:#9aa39d}
  .plan li.no::before{background:var(--amber-bg);border-color:#e2c46a}
  .plan .buy{margin-top:auto;text-align:center;font-weight:600;font-size:.9rem;padding:10px;border-radius:8px;border:1px solid var(--line);color:var(--ink)}
  .plan.best .buy{background:var(--accent);color:#fff;border-color:var(--accent)}
  .plan .buy:hover{border-color:#cfccc1}
  .plan.best .buy:hover{background:var(--accent-d)}
  .pnote{color:var(--muted);font-size:.85rem;font-family:var(--mono);margin-top:16px}

  /* cta band */
  .band{background:var(--ink);color:#fff;border-radius:18px;padding:52px 34px;text-align:center;margin:8px 0 60px}
  .band .eyebrow{color:#5fd08a;display:block;margin-bottom:12px}
  .band h2{color:#fff;margin-bottom:10px}
  .band p{color:#b8c6bd;max-width:520px;margin:0 auto 26px}

  footer{border-top:1px solid var(--line);padding:26px 0 56px;color:var(--muted);
    font-size:.84rem;font-family:var(--mono);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
</style>
</head>
<body>
<div class="wrap">
  <nav>
    <div class="logo">AS<b>V</b>A</div>
    <div class="links">
      <a href="#how">How it works</a>
      <a href="#why">Why ASVA</a>
      <a href="#pricing">Pricing</a>
      <a href="/download">Download</a>
      <a class="cta" href="https://wa.me/__WA__?text=I%20want%20to%20try%20ASVA">Talk to us &rarr;</a>
    </div>
  </nav>

  <header class="hero">
    <span class="eyebrow">TallyPrime &middot; WhatsApp &middot; Auto-collections</span>
    <h1>Your outstanding,<br><span class="g">collected on its own.</span></h1>
    <p class="lede">ASVA reads your TallyPrime ledger and sends every bill and payment reminder
      on WhatsApp, from your own number. You stop chasing customers, and the money comes in sooner.</p>
    <div class="cta-row">
      <a class="btn btn-p" href="https://wa.me/__WA__?text=I%20want%20to%20try%20ASVA">Talk to us on WhatsApp</a>
      <a class="btn btn-s" href="#how">See how it works</a>
    </div>
    <div class="undernote">Set up in ~5 minutes &middot; uses your existing WhatsApp &middot; your books never leave your PC</div>
  </header>

  <div class="strip">
    Built for <b>electrical, hardware, chemical, steel, paint and pipe</b> distributors running <b>TallyPrime</b>.
  </div>

  <section id="how">
    <div class="head">
      <span class="eyebrow">How it works</span>
      <h2>Three steps, then it runs itself</h2>
      <p>Your ledger stays the source of truth. ASVA reads it and does the chasing for you.</p>
    </div>
    <div class="steps">
      <div class="step"><div class="n">STEP 01</div><h3>Connect Tally</h3>
        <p>A small app sits next to TallyPrime and reads your sales and receipts. No exports, no spreadsheets, no cloud upload of your books.</p></div>
      <div class="step"><div class="n">STEP 02</div><h3>ASVA sends on WhatsApp</h3>
        <p>New bills go out with the PDF. Overdue accounts get polite, timed reminders with a UPI pay link, all from your own number.</p></div>
      <div class="step"><div class="n">STEP 03</div><h3>You get paid</h3>
        <p>Payments reconcile from Tally automatically. Each evening you get a WhatsApp digest of new bills, money collected, and who to call.</p></div>
    </div>
  </section>

  <section id="why">
    <div class="head">
      <span class="eyebrow">Why ASVA</span>
      <h2>A recovery agent, not another reminder app</h2>
      <p>Built around one job: getting your outstanding paid, without annoying your customers.</p>
    </div>
    <div class="feat">
      <div class="f"><h3><span class="dot"></span>From your own number</h3><p>Customers hear from your shop, not an unknown brand. Trust and relationships stay intact.</p></div>
      <div class="f"><h3><span class="dot"></span>Timed, never spammy</h3><p>Reminders follow each party's credit terms and back off the moment they reply. No blasting, no bans.</p></div>
      <div class="f"><h3><span class="dot"></span>Tally stays the truth</h3><p>ASVA never edits your books. It reads what is outstanding and confirms payments back from Tally.</p></div>
      <div class="f"><h3><span class="dot"></span>End-of-day digest</h3><p>One clean WhatsApp summary each night: new bills, collections, and the accounts worth a call.</p></div>
    </div>
  </section>

  <section id="pricing">
    <div class="head">
      <span class="eyebrow">Pricing</span>
      <h2>Priced by active debtors</h2>
      <p>You pay for the customers ASVA actually chases, not per message. Simple, monthly, direct.
        Every plan sends bills, reminders and the digest. Growth and above add the WhatsApp owner assistant.</p>
    </div>
    <div class="price">
      <div class="plan">
        <div class="tag">&nbsp;</div>
        <div class="name">Basic</div>
        <div class="amt">&#8377;699<span class="per">/mo</span></div>
        <div class="cap">Up to 300 debtors</div>
        <ul>
          <li>Auto bills and reminders</li>
          <li>UPI pay links</li>
          <li>End-of-day digest</li>
          <li class="no">No owner assistant</li>
        </ul>
        <a class="buy" href="https://wa.me/__WA__?text=Start%20ASVA%20Basic">Get started</a>
      </div>
      <div class="plan best">
        <div class="tag">Most popular</div>
        <div class="name">Growth</div>
        <div class="amt">&#8377;1,099<span class="per">/mo</span></div>
        <div class="cap">Up to 500 debtors</div>
        <ul>
          <li>Everything in Basic</li>
          <li>WhatsApp owner assistant</li>
          <li>Ask balances, send reminders by chat</li>
          <li>Photo-bill capture</li>
        </ul>
        <a class="buy" href="https://wa.me/__WA__?text=Start%20ASVA%20Growth">Get started</a>
      </div>
      <div class="plan">
        <div class="tag">&nbsp;</div>
        <div class="name">Pro</div>
        <div class="amt">&#8377;1,999<span class="per">/mo</span></div>
        <div class="cap">Up to 1,000 debtors</div>
        <ul>
          <li>Everything in Growth</li>
          <li>Higher daily send volume</li>
          <li>Priority support</li>
        </ul>
        <a class="buy" href="https://wa.me/__WA__?text=Start%20ASVA%20Pro">Get started</a>
      </div>
      <div class="plan">
        <div class="tag">&nbsp;</div>
        <div class="name">Custom</div>
        <div class="amt" style="font-size:1.4rem;margin-top:16px">Let's talk</div>
        <div class="cap">1,000+ debtors</div>
        <ul>
          <li>Everything in Pro</li>
          <li>Multiple companies</li>
          <li>Onboarding done for you</li>
        </ul>
        <a class="buy" href="https://wa.me/__WA__?text=Custom%20ASVA%20plan">Contact us</a>
      </div>
    </div>
    <p class="pnote">Pay directly by UPI. No setup fee. Cancel anytime.</p>
  </section>

  <div class="band">
    <span class="eyebrow">Get started</span>
    <h2>See your first reminders go out today</h2>
    <p>Send us a message and we will connect your Tally and set up your first batch together.</p>
    <a class="btn btn-p" href="https://wa.me/__WA__?text=I%20want%20to%20try%20ASVA">Talk to us on WhatsApp</a>
  </div>

  <footer>
    <span>&copy; 2026 ASVA &middot; Collect faster. Stop chasing.</span>
    <span><a href="mailto:__EMAIL__">__EMAIL__</a></span>
  </footer>
</div>
</body>
</html>"""


def landing_html() -> str:
    return (LANDING_HTML
            .replace("__WA__", CONTACT_WA)
            .replace("__EMAIL__", CONTACT_EMAIL))
