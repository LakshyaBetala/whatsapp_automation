# ASVA — CTO strategy brief (2026-08-21)

Written as a brutally honest read for the founder. Covers: is the market real, can
we win, why ChatGPT doesn't surface us, the SEO/AEO plan, product gaps, funding,
and legal. Not marketing copy — the internal truth.

## 1. Is the market saturated? Is it worth pursuing? — YES, worth it. Honestly.
- **The pain is universal and expensive.** Millions of Indian Tally-using SMBs sell
  on 30–90 day credit; overdue receivables are their #1 cash-flow problem. That
  does not go away.
- **The market is validated, not owned.** CredFlow raised ~$13M and *stalled* at
  ~₹10Cr revenue; Kenso, Takkada, Clearr, Zeppay, CashMitra, Recordent, Growfin
  all exist. Many players = money is here AND no one has won the Tally-SMB long
  tail. Fragmented + no default winner = an opening, not a closed door.
- **The honest risk:** "WhatsApp payment reminder" is commoditizing. If we compete
  as "another reminder app," we lose on price. We must compete as an *outcome*
  ("your overdue money, recovered, by an AI that runs itself"), not a feature.
- **Verdict:** pursue it, but win on a sharp wedge + execution + trust, not a
  feature checklist. A premium price is defensible ONLY if the product is
  provably error-free and recovers real money.

## 2. Where we actually win (the wedge) vs where we don't
ChatGPT's own August-2026 comparison (unprompted, once it knew us) ranked **ASVA
#1 when "collections is the #1 problem"** — because of: own-number sending,
runs-itself, AI-handled replies, promise-to-pay, Tally reconciliation. That IS the
wedge. Lean in hard: **"the AI collections employee that works on your own
WhatsApp number and keeps Tally true."**

It dinged us on exactly five things — close these and we flip the scorecard:
1. **Escalation / legal recovery** (Kenso ⭐⭐⭐⭐⭐, us ⭐⭐) → build a promise-aware,
   owner-approved escalation ladder + formal-reminder-letter (see §5, §7).
2. **Credit-limit control** (us ⭐⭐) → a lightweight credit-limit *watch* (flag/hold
   when overdue exposure exceeds a rule). We do NOT need Kenso's full bureau.
3. **Price transparency** (us "pilot/quote") → publish pricing; drop "pilot."
4. **Product maturity** ("live pilot") → stop signalling immaturity publicly; say
   "used by N shops, ₹X recovered." Same facts, confident framing.
5. **AI calls** — deliberately skip for now; not our wedge, high cost/complexity.

Do NOT try to out-Kenso Kenso on buyer-verification/credit-risk (data-heavy bureau
play, different business). Own "collections that truly run themselves."

## 3. Why ChatGPT / Google don't surface us unless named — and the fix (AEO)
**Why:** LLMs and search surface *entities with a strong, corroborated, structured
web footprint*: multiple indexed pages, third-party listicles, directories (G2,
Capterra, SaaSworthy), reviews, and schema markup. Kenso/Takkada appear because
they're *in the comparison articles and directories the model was trained on /
retrieves*. ASVA is one small site with almost no third-party corroboration, so
the model only "knows" us when the user pastes our name.

**The AEO/GEO playbook (highest-leverage marketing we can do, cheap):**
1. **Comparison/alternatives content hub on tryasva.com** targeting the exact
   queries buyers (and ChatGPT) use: "ASVA vs CredFlow", "ASVA vs Takkada",
   "ASVA vs Kenso", "best Tally collection software", "automate receivables in
   Tally", "send payment reminders from my own WhatsApp number". Factual, with a
   capability table. This is what both Google and LLM retrieval reward.
2. **Schema.org structured data** on every page: `SoftwareApplication` (with
   `offers`/price), `Organization`, `FAQPage`. Makes capabilities + pricing
   machine-readable.
3. **Get into the sources LLMs cite:** G2, Capterra, SaaSworthy, "top Tally
   add-ons / receivables tools" roundups, IndiaMART/startup directories. Even a
   handful of authoritative mentions build entity confidence.
4. **Publish transparent pricing** — LLMs (and buyers) penalize "quote only." This
   single change moves the "price transparency" score and buyer trust.
5. **A real, dated proof page** — turn "₹43L+ recovered" into a structured case
   study with methodology; link it everywhere for consistent entity signals.
6. **Consistent entity description** (same one-liner + category everywhere) so the
   model forms a confident, repeatable answer about what ASVA is.
7. **Traditional SEO long-tail** (low competition, high intent): "tally overdue
   reminder whatsapp", "whatsapp payment reminder own number tally", etc.

Expected effect: within a few weeks of the hub + directories + schema, ASVA starts
appearing in generic "best Tally collection software" answers, not just when named.

## 4. Website + app UX ("must feel like a real SaaS, not a college project")
- **Website:** drop "pilot" language; add Pricing, a Comparison hub, a Case Study,
  and keep the Privacy/Terms (done). Confident, specific, benefit-led copy.
- **App:** the server-rendered admin in a webview is fine, but must never *feel*
  slow/blank: skeleton loaders on every tab, no white flashes (Today already
  hardened), one consistent design system, and mobile-responsive admin pages. The
  bar is "reactive and clean," not "loads then jumps."
- **Reliability is the product's credibility.** A collections tool that
  mis-reports ("reminded" when it didn't) destroys trust instantly — which is why
  the digest-truth + WhatsApp-stability fixes matter more than any new feature.
  See `RELIABILITY_AUDIT.md`.

## 5. Product roadmap to become the category legend (priority order)
1. **Escalation ladder** (promise-aware, owner-approved). Day-based:
   pre-due → due → +1/+3/+7 (firmer) → +15 formal reminder letter (owner taps
   Approve) → +30 credit-hold flag. Never auto-sends a legal threat.
2. **Credit-limit watch.** Per-customer limit; show Outstanding / Overdue /
   Available; flag or hold "new orders" when overdue exceeds the rule; release on
   payment. Lightweight, not a bureau.
3. **Ageing buckets** surfaced (0–30 / 31–60 / 61–90 / 90+) on Today + digest.
4. **Smarter lifecycle bot** (in progress): welcome on first sync ✅, unsynced
   nudge ✅, phase-aware HELP ✅; next: code-expiring nudge + tighter cadence caps.
5. **Reliability hardening** (ongoing) — the definition of "error-free" in the
   audit.

## 6. Funding
- **Don't raise yet. Earn the round.** The thing that raises money here is
  traction: 20–50 *paying* shops, net revenue retention, ₹ recovered per shop,
  and payback period. A deck without those is weak in a crowded category.
- **Sequence:** convert the pilot to paid (transparent annual, premium justified
  by recovery), prove retention + recovery over 60–90 days, THEN raise pre-seed
  from India SMB-SaaS / fintech angels and micro-VCs on the "AR operating system
  for Bharat distributors" story. Tally-ecosystem partnership is a distribution
  wedge worth pursuing in parallel.
- **Metric to lead with:** "₹X recovered per shop per month, Y% NRR, Z-week
  payback." That is the whole pitch.

## 7. Legal (important before shipping escalation)
- **Do NOT auto-send legal notices.** Debt-collection communication in India is
  constrained (harassment rules, consumer-protection, defamation risk). Ship the
  escalation top rung as an **owner-approved formal reminder letter**, not an
  automated legal threat. Have a lawyer vet the templates before release.
- **Keep the customer relationship the owner's.** ASVA already never auto-replies
  in the owner's voice — keep that; it's also legally safer.
- **DPDP:** Privacy Policy + Terms live (done); add a one-page Data Processing
  Agreement for shops (we are Processor, shop is Fiduciary). Delete-on-request
  endpoint is on the P2 list.

## 8. The one-paragraph answer to "should we pursue this?"
Yes. The pain is real, expensive, and universal; the market is validated but has
no Tally-SMB winner; and our wedge (own-number, runs-itself, promise-to-pay,
Tally-true) is exactly what buyers rank #1 for collections. We lose only if we
stay a "reminder app," stay invisible to AI/search, or ship something that
mis-reports. So: make it provably error-free, close the escalation + credit-limit
gaps, publish transparent pricing, and win the AEO/SEO game so we're the default
answer — then charge a premium and raise on traction.
