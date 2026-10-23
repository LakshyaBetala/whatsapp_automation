# ASVA economics, pricing and infrastructure decisions

**Date:** 24 October 2026
**Status:** decided; code changes shipped in commits `00bfc30` and `57ec005`

Everything here is built on the live database and the real GCP invoice, not on
estimates. Measured figures are labelled; modelled ones say so.

---

## 1. The plan ladder

| Plan | Price | Active debtors | Monthly messages | Daily cap |
|---|---|---|---|---|
| Lite | Rs 699 | 100 | 900 | 50 |
| Collect | Rs 999 | 250 | 2,250 | 100 |
| Recover | Rs 1,499 | 500 | 4,000 | 150 |
| Custom | contact | 2,000 | 16,000 | 300 |

**Annual = 11x monthly** (pay eleven months, get twelve). Lite Rs 7,689,
Collect Rs 10,989, Recover Rs 16,489. Not 10x, not two months free.

### Why these numbers

Active debtors per real shop, counted from `bills` on 24 October:

```
SUPREME PETRO CHEMICALS   286      (Tally ledger: 1,000 rows)
RISHAB TRADING            176
G AND G MARKETING         124
PROPAC SALES               88
SURBHI ENTERPRISES         53
SRI SHANKAR AGENCIES       53
Rtc Coinage                 8
New Generation InfoTech     1
```

An **active debtor** is a party with a WhatsApp number *and* an open bill. It is
not the size of the Tally ledger. SUPREME carries 1,000 ledger rows but only 286
parties who actually owe money. That distinction is the whole reason this ladder
works: priced on ledger size, every one of these shops would need the top tier.

The old ladder was 150 / 400 / 1,000. The top tier was priced for a shop that
does not exist, and the median shop sat comfortably inside Lite, which is the
wrong place for a Rs 999 average to come from.

### Why the message caps changed

They were 1,200 / 3,200 / 8,000. On Baileys a message costs nothing, so the only
reason to cap was ban safety, and `daily_cap` already does that job. The moment
delivery is billed per message, the monthly cap stops being a safety rail and
becomes the ceiling on what a single subscription can lose: 8,000 messages at
Rs 0.16 is Rs 1,280 against a Rs 1,499 plan.

900 / 2,250 / 4,000 is roughly nine messages per party per month, which leaves
room for manual Send Now on top of the automatic cadence. It is also a product
guardrail: 4,500 messages to 500 parties would be nine a month each, which is
harassment, not collections.

Custom dropped from 5,000 debtors to 2,000. 5,000 was imaginary; 2,000 is a real
large distributor and the point where the official API stops being optional.

---

## 2. What it costs

### Measured

GCP billing, three days (the whole invoice):

| Service | 3 days | Share |
|---|---|---|
| Compute Engine | Rs 344.95 | 94% |
| Networking | Rs 20.05 | 5.5% |
| Vertex AI | Rs 0.26 | 0.07% |

About Rs 123/day. Other measured values: 85-106 MB RAM per Baileys process,
Rs 0.02 per Gemini classification, 58 messages sent per shop per month.

**Do not divide the Vertex total by twelve.** All 19 inbound messages in the
database, ever, are from RISHAB TRADING COMPANY. The AI is barely running.
Extrapolate from the per-call figure instead.

### Modelled: 100 shops, after pooling

| | per month |
|---|---|
| e2-standard-2 (8 GB), 1-year commit | Rs 3,400 |
| Disk + static IP | Rs 950 |
| Networking (measured, scaled) | Rs 1,700 |
| Supabase Pro | Rs 2,100 |
| **Fixed total** | **Rs 8,150** (Rs 85/shop) |

### Unit economics per tier

Worst case, every month at the cap, on the official API:

| | Lite | Collect | Recover |
|---|---|---|---|
| Meta templates @ Rs 0.16 | 144 | 360 | 640 |
| Service replies over 1,000 free | 0 | 0 | 80 |
| Gemini | 20 | 40 | 70 |
| Infra share | 85 | 85 | 85 |
| **COGS** | **249** | **485** | **875** |
| **Margin** | **64%** | **51%** | **42%** |

Realistic, at three messages per party per month:

| | Lite | Collect | Recover |
|---|---|---|---|
| **COGS** | **141** | **225** | **365** |
| **Margin** | **80%** | **77%** | **76%** |

On Baileys today there is no Meta line at all: Lite COGS is Rs 105 and the
margin is 85%.

**Recover at 42% worst case is the weak tier.** It is where one heavy user costs
real money. Lean on the Meta volume discount there.

### 100 shops, full year, Collect average, annual billing

| | per year | per month |
|---|---|---|
| Revenue (ex-GST) | 10,98,900 | 91,575 |
| Variable (Meta + Gemini) | 1,68,000 | 14,000 |
| Fixed infra | 97,800 | 8,150 |
| **Gross profit** | **8,33,100** | **69,425** |

Break-even is **5 shops** on today's e2-medium with free Supabase, **10** once
on the 8 GB box with Supabase Pro.

The annual plan's real value is not the 8.3% discount, it is **Rs 8.3 lakh of
float** collected before it is spent.

---

## 3. GST

Three facts, and the third is a sales argument.

1. **It is not revenue.** Rs 999 + 18% = Rs 1,178.82. You collect Rs 179.82 and
   remit Rs 179.82. Never quote GST-inclusive, never absorb it.
2. **Input credit offsets it.** Google Cloud India and Meta India both bill 18%,
   reclaimable. Supabase is a foreign supplier, so reverse charge: pay 18%, claim
   it straight back. At 100 shops, Rs 1,97,802 collected minus Rs 47,880 of input
   credit leaves Rs 1,49,922 remitted.
3. **Customers are GST-registered wholesalers and claim ITC**, so "Rs 699 + GST"
   costs the shop Rs 699 net. The tax is invisible to their real cost, and a
   vendor without a GSTIN is one they cannot claim against. Having one makes ASVA
   more sellable, not less.

Invoice requirements: LLP name and GSTIN, customer GSTIN, place of supply,
**SAC 998314**, IGST 18% (usually interstate) or CGST 9% + SGST 9%. GSTR-1 and
GSTR-3B monthly. E-invoicing starts at Rs 5 crore turnover, not applicable.

At 100 shops turnover is about Rs 12 lakh, under the Rs 20 lakh services
threshold, so registration is optional. Register anyway, for point 3.

---

## 4. Meta billing architecture

A WABA holds phone numbers. You cannot put 100 shop numbers in one: the limit is
two before Meta verifies your business and twenty after.

The route that works is **Tech Provider**:

- Each shop gets **its own WABA**, created when they click through Meta's popup
  on the ASVA site (Embedded Signup). The shop owns it; ASVA manages it.
- Every WABA sits inside **one Meta Business Portfolio**.
- One payment method on that portfolio means **one bill for all shops**.

Three pieces of work: Meta business verification (free, weeks, start now),
the Tech Provider application, and building Embedded Signup.

**Volume discounts pool across every number in one portfolio** (5-20%, by
country and template type, non-retroactive). A hundred separate accounts would
each pay full rate. This is an independent second reason for the architecture.

### Changed 1 October 2026

In-window service replies used to be free. They are now billed at the utility
rate, with **1,000 free service messages per number per month**. Incoming
messages from customers stay free.

That allowance covers every realistic shop; only a Recover shop at full tilt
crosses it. **This is the most volatile number in the model** - it moved three
weeks ago and Meta's own pages still contradicted it on 13 October. Re-verify
before pricing on it.

---

## 5. Transport: Baileys now, official API later

Stay on Baileys. Zero marginal cost, "sends from your own number" is a real
differentiator, and the ban controls are already built.

**Switch triggers:** the first 403 ban, or roughly 40 shops.

Start Meta business verification immediately regardless. It is free and takes
weeks, so doing it now decouples the paperwork from the migration.

Long-term shape: Lite on Baileys, Recover on the official API by default. That
gives Rs 1,499 a reason to exist that is not "more rows" - *we carry the ban
risk, not you*.

---

## 6. AI gating

Split it. **Gate the chase, not the reply.**

| Feature | Tier | Reason |
|---|---|---|
| Reply understanding (reads "paid", "will pay Friday", pauses cadence, logs the promise) | every tier | Two-way traffic is what stops a number looking like a spammer. Denying it to Lite gives the smallest, newest, most fragile shops the worst ban profile. Costs Rs 8/month. |
| Smart chase (who to chase today, behaviour ranking, promise follow-up, recovery file) | Collect 999+ | About the owner's decisions, not the customer's safety. Felt every morning. Worth Rs 300. |

A Lite shop with 100 parties gets perhaps 20 replies a month, so withholding
reply handling is a weak upgrade lever anyway. "ASVA tells you who to chase this
morning" is something they would miss daily.

---

## 7. Database: self-hosted Postgres, not Cloudflare D1

Cloudflare D1 is SQLite. The backend relies on Postgres enums and on the
`increment_usage_if_allowed` RPC doing `SELECT ... FOR UPDATE` - the atomic
check-and-increment that stops double-sending. Moving to D1 means rewriting every
query and reimplementing that guarantee, to save roughly Rs 1,100 a month.

**Put Postgres on the VM that is already paid for.**

- Saves the whole Rs 2,100 of Supabase Pro; costs about Rs 400 of extra RAM.
- Kills the ~250 ms round trip per query. That latency is the single largest
  source of slowness in the product - the dashboard was doing six sequential
  round trips before it was parallelised.
- Zero rewrite: Postgres to Postgres, a `pg_dump` and a restore.
- Already the stated direction in the vision memory.

The cost is becoming your own DBA. Mitigate with automated `pg_dump` to
Cloudflare R2 plus the off-box backup pull already specified in the reliability
plan.

**Cloudflare still earns a place for R2**, which has no egress fees: invoice PDFs
behind signed URLs, and database backups. Use it for storage, not for the
database.

---

## 8. Road to Rs 10 lakh per month

Rs 10,00,000 / Rs 999 = **about 1,000 shops**.

At that size infrastructure is still only 6-8% of revenue and gross margin holds
near 75%. The constraints are elsewhere:

- **Distribution.** The open problem.
- **Onboarding.** Every shop needs Tally setup and a WhatsApp pairing.
- **Support.** Roughly one person per 500 shops at ten minutes per shop per month.

Infrastructure is never the limit. Do not optimise it at the expense of the
three above.

---

## 9. The explainer video

### Pipeline

Remotion (React that renders MP4) driven by real screenshots, not motion
graphics. Three reasons it fits: the product is already a web UI, the Playwright
and CDP harness built for E2E testing can walk the real app and capture every
frame, and `npx remotion render` runs headless overnight with no video editor
and no human in the loop.

```
Playwright walks the real app  ->  PNG frames per step
            |
            v
Remotion composition (React)  ->  pans, callouts, captions, timing
            |
            v
npx remotion render           ->  MP4, overnight
```

Voiceover recorded by the founder, not TTS. For this audience a real Indian
voice explaining their own problem outperforms a synthetic one, and it is the
only part a machine should not do.

### Structure, 90 seconds, one message: *it runs itself*

| Time | Beat |
|---|---|
| 0-10s | The problem. The real Tally screen, the real outstanding figure. "Who do you chase today?" |
| 10-25s | ASVA reads Tally. No plugin, no TDL. Show the sync completing. |
| 25-45s | A reminder leaves the shop's own WhatsApp number. Show the actual phone. |
| 45-60s | The customer replies "paid". ASVA understands and pauses the chase. |
| 60-75s | The owner confirms; the receipt posts into Tally. |
| 75-90s | The morning digest: who paid, who to chase. |

Keep the existing colour theme and phone mock exactly as they are.

Ship English first; a Hindi cut second. Resist animation for its own sake - for
Indian wholesale distribution, a real screen with real numbers is more
persuasive than motion graphics, and the loop closing is the whole pitch.

---

## 10. Open items

| Item | Note |
|---|---|
| 403 reconnect loop | `wa_service/index.js:589` reconnects into a ban every 5 min and tells nobody. Highest-value fix. |
| Day-one warm-up | A new 500-party shop sends 20 messages on day one. Needs an onboarding line and a dashboard progress row, not a higher cap. |
| Daily-cap-reached alert | Does not exist. `debtor_cap_notice` covers debtor overflow only. |
| Parties selection UX | Ideated, not built. Separate selection from the reminder toggle first. |
| Session pooling | ~10 shops per Node process. Worth doing around 40 shops. |
| Postgres on the VM | Replaces Supabase Pro; also removes the 250 ms round trip. |
| Cloudflare token | Expired; tryasva.com cannot publish. Needs `npx wrangler login`. |
| Operator key rotation | Spent weeks in logs. |
| Meta business verification | Not started. Start now - it is the long pole. |
