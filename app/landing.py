"""The public ASVA landing page, served server-side at GET /.

Self-contained HTML (inline CSS, system fonts, no external requests) so it loads
instantly through the Cloudflare tunnel with nothing to fetch. Plain marketing
copy, English, no em dashes (house style). Edit CONTACT_WA / CONTACT_EMAIL to
your real details before going live.
"""
from __future__ import annotations

CONTACT_WA = "919444294894"          # wa.me number for "Talk to us" (change me)
CONTACT_EMAIL = "hello@tryasva.com"  # change me

LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASVA - Collect Faster. Stress Less.</title>
<meta name="description" content="ASVA connects to your TallyPrime and automatically sends bills and payment reminders on WhatsApp, so your distribution business gets paid faster.">
<style>
  :root{
    --bg:#f7f5f0; --card:#ffffff; --ink:#1c2620; --muted:#5d6b62;
    --line:#e7e3da; --green:#0a7d33; --green-d:#0c8f3b; --paleg:#eef4ee;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:'SF Pro Text',-apple-system,'Segoe UI',system-ui,sans-serif;
    line-height:1.6;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:0 22px}
  a{color:inherit}
  .serif{font-family:'Iowan Old Style','Palatino Linotype',Georgia,'Times New Roman',serif}
  .btn{display:inline-block;font-weight:600;font-size:.98rem;padding:12px 22px;border-radius:8px;
    text-decoration:none;transition:transform .12s ease,background .15s ease}
  .btn:active{transform:scale(.98)}
  .btn-p{background:var(--green);color:#fff}.btn-p:hover{background:var(--green-d)}
  .btn-s{background:#fff;color:var(--ink);border:1px solid var(--line)}
  .btn-s:hover{border-color:#cfcabf}

  /* nav */
  nav{display:flex;align-items:center;justify-content:space-between;padding:20px 0}
  .logo{font-weight:800;letter-spacing:.14em;font-size:1.05rem}
  .logo b{color:var(--green)}
  nav .links a{margin-left:24px;text-decoration:none;color:var(--muted);font-size:.92rem}
  nav .links a:hover{color:var(--ink)}
  @media(max-width:640px){nav .links{display:none}}

  /* hero */
  .hero{padding:64px 0 40px;max-width:760px}
  .eyebrow{display:inline-block;background:var(--paleg);color:var(--green);
    font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
    padding:5px 12px;border-radius:999px;margin-bottom:22px}
  h1{font-size:clamp(2.3rem,6vw,4rem);line-height:1.06;letter-spacing:-.02em;margin:0 0 20px;text-wrap:balance}
  .lede{font-size:1.2rem;color:var(--muted);margin:0 0 30px;max-width:600px}
  .cta-row{display:flex;gap:12px;flex-wrap:wrap}
  .undernote{margin-top:16px;color:var(--muted);font-size:.86rem}

  /* strip */
  .strip{border-top:1px solid var(--line);border-bottom:1px solid var(--line);
    padding:20px 0;margin:44px 0;color:var(--muted);font-size:.9rem;
    display:flex;gap:26px;flex-wrap:wrap;justify-content:space-between}
  .strip b{color:var(--ink);font-variant-numeric:tabular-nums}

  /* sections */
  section{padding:40px 0}
  .kicker{font-size:.75rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green);margin-bottom:10px}
  h2{font-size:clamp(1.7rem,3.4vw,2.5rem);letter-spacing:-.02em;margin:0 0 10px;text-wrap:balance}
  .sub{color:var(--muted);max-width:620px;margin:0 0 34px}

  .steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  @media(max-width:760px){.steps{grid-template-columns:1fr}}
  .step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px}
  .step .n{width:34px;height:34px;border-radius:9px;background:var(--paleg);color:var(--green);
    font-weight:800;display:flex;align-items:center;justify-content:center;margin-bottom:16px}
  .step h3{margin:0 0 6px;font-size:1.16rem}
  .step p{margin:0;color:var(--muted);font-size:.96rem}

  .feat{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
  @media(max-width:760px){.feat{grid-template-columns:1fr}}
  .f{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px}
  .f h3{margin:0 0 6px;font-size:1.06rem}
  .f p{margin:0;color:var(--muted);font-size:.94rem}

  /* pricing */
  .price{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
  @media(max-width:860px){.price{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:480px){.price{grid-template-columns:1fr}}
  .plan{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px}
  .plan.best{border-color:var(--green);box-shadow:0 0 0 1px var(--green)}
  .plan .name{font-weight:700}.plan .tag{font-size:.72rem;color:var(--green);font-weight:700}
  .plan .amt{font-size:1.8rem;font-weight:800;margin:8px 0 2px}
  .plan .amt span{font-size:.9rem;color:var(--muted);font-weight:500}
  .plan .who{color:var(--muted);font-size:.88rem;margin:0}

  /* cta band */
  .band{background:var(--ink);color:#fff;border-radius:20px;padding:48px 34px;text-align:center;margin:20px 0 60px}
  .band h2{color:#fff}.band p{color:#c7d2cb;max-width:520px;margin:0 auto 26px}

  footer{border-top:1px solid var(--line);padding:28px 0 50px;color:var(--muted);font-size:.86rem;
    display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
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
      <a href="mailto:__EMAIL__">Contact</a>
    </div>
  </nav>

  <header class="hero">
    <span class="eyebrow">For Indian distributors on Tally</span>
    <h1 class="serif">Collect faster.<br>Stress less.</h1>
    <p class="lede">ASVA connects to your TallyPrime and automatically sends every bill and
      payment reminder on WhatsApp, from your own number. You stop chasing customers, and
      the money comes in sooner.</p>
    <div class="cta-row">
      <a class="btn btn-p" href="https://wa.me/__WA__?text=I%20want%20to%20try%20ASVA">Talk to us on WhatsApp</a>
      <a class="btn btn-s" href="#how">See how it works</a>
    </div>
    <div class="undernote">Connect Tally in about 5 minutes. Works with your existing WhatsApp number.</div>
  </header>

  <div class="strip">
    <span>Built for <b>electrical, hardware, chemical, steel, paint &amp; pipe</b> distributors</span>
    <span>Runs on <b>your Tally ledger</b>, no new data entry</span>
  </div>

  <section id="how">
    <div class="kicker">How it works</div>
    <h2 class="serif">Three steps, then it runs on its own</h2>
    <p class="sub">Your ledger is the source of truth. ASVA reads it and does the chasing for you.</p>
    <div class="steps">
      <div class="step"><div class="n">1</div><h3>Connect Tally</h3>
        <p>A small app sits next to Tally and reads your sales and receipts. No exports, no spreadsheets, no cloud upload of your books.</p></div>
      <div class="step"><div class="n">2</div><h3>ASVA sends on WhatsApp</h3>
        <p>New bills go to the customer with the PDF. Overdue accounts get polite, timed reminders with a UPI pay link, all from your own number.</p></div>
      <div class="step"><div class="n">3</div><h3>You get paid</h3>
        <p>Payments update from Tally automatically. Every evening you get a WhatsApp digest of new bills, money collected, and who to call.</p></div>
    </div>
  </section>

  <section id="why">
    <div class="kicker">Why ASVA</div>
    <h2 class="serif">A recovery agent that runs itself</h2>
    <p class="sub">Not another reminder app. ASVA is built around one job: getting your outstanding paid.</p>
    <div class="feat">
      <div class="f"><h3>From your own number</h3><p>Customers hear from your shop, not an unknown brand. Trust stays intact.</p></div>
      <div class="f"><h3>Timed, not spammy</h3><p>Reminders follow each party's credit terms and back off after a reply. No blasting, no bans.</p></div>
      <div class="f"><h3>Tally stays the truth</h3><p>ASVA never edits your books. It reads outstanding and confirms payments back from Tally.</p></div>
      <div class="f"><h3>End of day digest</h3><p>One clean summary each night: new bills, collections, and the accounts worth a call.</p></div>
    </div>
  </section>

  <section id="pricing">
    <div class="kicker">Pricing</div>
    <h2 class="serif">Priced by active debtors</h2>
    <p class="sub">You pay for the customers ASVA actually chases, not per message. Simple, monthly, direct.
      Every plan sends bills and reminders. Growth and above add the WhatsApp owner assistant.</p>
    <div class="price">
      <div class="plan">
        <div class="name">Basic</div>
        <div class="amt">&#8377;699<span>/mo</span></div>
        <p class="who">Up to 300 active debtors</p>
        <p class="who">Bills + reminders + digest</p>
        <p class="who" style="color:#b06a00">No owner assistant</p>
      </div>
      <div class="plan best">
        <div class="tag">MOST POPULAR</div>
        <div class="name">Growth</div>
        <div class="amt">&#8377;1,099<span>/mo</span></div>
        <p class="who">Up to 500 active debtors</p>
        <p class="who" style="color:#346538">WhatsApp owner assistant</p>
      </div>
      <div class="plan">
        <div class="name">Pro</div>
        <div class="amt">&#8377;1,999<span>/mo</span></div>
        <p class="who">Up to 1,000 active debtors</p>
        <p class="who" style="color:#346538">WhatsApp owner assistant</p>
      </div>
      <div class="plan">
        <div class="name">Custom</div>
        <div class="amt">Let's talk</div>
        <p class="who">Larger shops, everything included</p>
        <a class="who" href="https://wa.me/__WA__?text=Custom%20ASVA%20plan" style="color:#346538">Contact us &rarr;</a>
      </div>
    </div>
  </section>

  <div class="band">
    <h2 class="serif">See your first reminders go out today</h2>
    <p>Send us a message and we will connect your Tally and set up your first batch together.</p>
    <a class="btn btn-p" href="https://wa.me/__WA__?text=I%20want%20to%20try%20ASVA">Talk to us on WhatsApp</a>
  </div>

  <footer>
    <span>&copy; 2026 ASVA. Collect Faster. Stress Less.</span>
    <span><a href="mailto:__EMAIL__" style="text-decoration:none;color:var(--muted)">__EMAIL__</a></span>
  </footer>
</div>
</body>
</html>"""


def landing_html() -> str:
    return (LANDING_HTML
            .replace("__WA__", CONTACT_WA)
            .replace("__EMAIL__", CONTACT_EMAIL))
