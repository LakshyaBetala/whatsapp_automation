# Distribution, brand and the road to 1,000 shops

**Date:** 24 October 2026
**Goal:** 1,000 paying shops by the end of 2027
**Today:** 1 paying, 12 on trial

Economics live in `DECISIONS-2026-10-24-economics.md`. This document is about
getting customers, which is the actual hard problem.

---

## 1. The shape of the problem

1,000 shops in fourteen months, from one. Direct selling cannot do this. At ten
minutes of support per shop per month, 1,000 shops is one full-time person per
500 just to answer the phone, plus someone to set up Tally and pair WhatsApp on
every single install.

So the question is not "how do we sell 1,000 times". It is **"who already sells
to, installs for, and supports this exact customer?"**

---

## 2. The crack: Tally partners

Tally distributes almost entirely through a certified channel of roughly
**28,000 partners** who handle sales, implementation, training and first-line
support. They already visit these shops. They already have the trust. They
already do the three things ASVA cannot afford to do itself at scale.

**1,000 shops is 60-80 active partners selling a dozen each.** That is a
recruitable number. 1,000 individual shop owners is not.

### Terms

25-30% of recurring revenue, for life, in exchange for the sale, the install and
first-line support.

| | Rs |
|---|---|
| Collect price | 999 |
| Partner share (25%) | -250 |
| COGS | -225 |
| **Net to ASVA** | **524 (52%)** |

Half the margin for all three of the hardest jobs is a good trade. The partner
gets recurring income off a customer they already own, which is exactly what a
Tally partner wants and cannot get from selling licences.

### Sequence

1. Recruit **five** partners, not fifty. Work with them personally until each has
   sold and successfully onboarded ten shops.
2. Write down what made those five work: the pitch, the objections, the install
   checklist, the first-week support script.
3. Only then recruit in volume, with that material as the partner kit.

A channel recruited before the playbook exists produces fifty partners who each
try once, fail, and never try again. That failure is very hard to undo.

### Partner kit (build before recruiting beyond five)

- One-page margin sheet: what they earn per shop per year.
- The 90-second video (below), which is also their sales call.
- Install checklist and a WhatsApp group for partner support.
- A demo account with realistic data.
- A partner view in the Command Center: their shops, their earnings, their
  renewals due.

### Second channel: CAs

A CA sees the receivables problem in every client's books and has no product to
point at. Lower volume than Tally partners, much higher trust per referral. Keep
it simple - a referral fee, no support obligation.

---

## 3. Integrations: the cheap answer first

The instinct is to build BUSY, Marg, Vyapar and Zoho connectors to widen the
market. Resist it for now, for two reasons.

**First, each connector is a new sync-reliability surface.** Sync failure is the
single biggest complaint about the market leader. Being excellent on Tally is
worth more than being mediocre on five platforms, and reliability is the thing
this market actually chooses on.

**Second, the universal connector already exists in the plan.** The owner
forwards their outstanding report - PDF or a photo - to the bot, and Gemini reads
it. That covers BUSY, Marg, Vyapar, Zoho, Excel and the shop that keeps a
notebook, at zero integration cost per platform.

**Ship WhatsApp-forward before building any second connector.** One Gemini call
replaces four ERP integrations. Build a native BUSY reader only when enough
forwarded reports prove the demand is concentrated there.

Order: Tally (done, keep hardening) -> WhatsApp-forward (universal) -> BUSY
native (only if the data says so) -> Marg (pharma vertical, only if a partner
asks).

---

## 4. Retention is worth more than acquisition

At 76-85% gross margin, a churned shop costs more than ten shops of server. At
1,000 shops, 3% monthly churn is 30 shops a month you must replace before you
grow at all.

**Show them the money.** The north-star metric is already decided: *Money
Recovered Through ASVA*. Put it on the dashboard and send it monthly on
WhatsApp: "ASVA recovered Rs 4.2 lakh for you this month." That message is the
renewal conversation, automated, and it arrives before the invoice does.

**Annual billing is the strongest retention tool available.** Twelve months of
guaranteed retention plus the float. Push it at every renewal.

**Deepen the switching cost honestly.** Once receipts post back into Tally and
the pay link is in every reminder, leaving ASVA means going back to doing it by
hand. That is earned lock-in, not a hostage situation.

**Watch the first week, not the first month.** A shop that sees its first
recovered payment in week one renews. One that hits the day-one warm-up confusion
with nobody explaining it churns before it ever sees value. The onboarding fixes
in the open items list are retention work, not polish.

---

## 5. Brand

**Name the category; do not compete inside someone else's.** "WhatsApp reminder
software" is a commodity and a Rs 150/month TallyPrime feature. ASVA is *the
collections agent for Tally shops* - it runs the chase, reads the reply, posts
the receipt, and tells you who to call. Positioning already decided: **the
recovery agent that runs itself**.

**Trust signals that work on this audience**, in order: a phone number that a
human answers, a real LLP with a GST invoice, the founder's face and name, a
shop in their own trade saying it worked. Not logo walls, not invented metrics,
not "trusted by 10,000+ businesses".

**The one claim to defend above all others is reliability.** The leader's
weakness is sync failure and vanished support. Every public number ASVA uses
should be one it can prove.

---

## 6. Legal escalation

Already decided, restated because it is both a revenue line and a positioning
risk:

- A **Recovery file** PDF with the MSME s.15/s.16/43B(h) section, included only
  when the shop is Udyam-registered.
- A **paid per-notice advocate add-on**. Lawyer reviews the wording before
  anything ships.
- **ASVA never sends a legal threat itself.** The file is handed to the owner;
  the escalation is theirs. This is what keeps the product on the right side of
  both the law and the "relationship-preserving" positioning that is the whole
  reason shops trust it with their own WhatsApp number.

As revenue it is small - at 1,000 shops with 2% using one notice a month, maybe
Rs 10,000 a month. Its real value is that it is the highest-stakes moment in the
customer relationship, and being present for it is what makes Recover worth
Rs 1,499.

---

## 7. The video

Covered in the economics doc section 9: Remotion driven by real Playwright
screenshots, 90 seconds, founder voiceover, one message - *it runs itself*.

Two things to add here, because the video is a distribution asset before it is a
marketing asset:

- **It is the partner's sales call.** A Tally partner will not learn a pitch.
  They will play a video on their phone in the shop. Design it to work with the
  sound off, with captions, in portrait as well as landscape.
- **Cut a 30-second version for WhatsApp forwarding.** This market shares on
  WhatsApp, not on LinkedIn. The 30-second cut will out-distribute the 90-second
  one by an order of magnitude.

---

## 8. Milestones

| By | Shops | What has to be true |
|---|---|---|
| Dec 2026 | 15 | Direct sales. Day-one onboarding fixed. Video shot. |
| Mar 2027 | 50 | First five partners live, playbook written. |
| Jun 2027 | 150 | ~20 partners. WhatsApp-forward shipped. Support under 10 min/shop/month. |
| Sep 2027 | 400 | ~40 partners. Partner view in Command Center. Official API path open. |
| Dec 2027 | 1,000 | 60-80 active partners. Churn under 2%/month. |

1,000 shops at Rs 999 is **Rs 9.99 lakh a month** - the Rs 10 lakh goal and the
1,000-shop goal are the same goal.

After a 25% channel share, that is roughly Rs 7.5 lakh net revenue, Rs 2.25 lakh
of COGS, and about **Rs 5.2 lakh a month of gross profit**.

---

## 9. What would stop this

Honestly, in order of probability:

1. **Onboarding friction.** Every shop needs Tally reachable, a WhatsApp pairing
   that survives, and a first week that works. This is the bottleneck, not sales.
2. **A correlated WhatsApp ban.** Covered in the ban-risk memo. One event takes
   out a cohort.
3. **Support load.** Fixed only by the channel doing first-line, which is why the
   partner model is structural and not just a cheaper way to sell.
4. **Tally shipping it themselves.** They have the native WhatsApp hook already,
   but it is manual and they have shown no interest in the recovery loop. If they
   do build it, the WhatsApp-forward universal reader is the hedge.

Nothing on this list is a cost problem. Do not spend the next year optimising
infrastructure.
