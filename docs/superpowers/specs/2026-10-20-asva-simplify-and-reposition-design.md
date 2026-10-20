# ASVA: simplify and reposition (design)

Date: 2026-10-20. Status: approved in brainstorming, awaiting spec review.

## Why

ASVA's buyer is a wholesaler with credit stuck. Their one job is: get the money
in without fighting with customers. Today the product spreads that job over 9
screens and 4 plans, and its cheapest story ("bill + reminder on WhatsApp") is
now a ₹150/month feature inside TallyPrime itself. What nobody else does is the
closed loop:

    chase on WhatsApp -> customer pays on the pay link -> payment matched ->
    written back to Tally -> AI handles the replies -> evidence ready if it goes legal

Every decision below pushes the owner into that loop and removes what is not
part of it.

## Competitor read (October 2026)

| Player | What they sell | Price | Gap we exploit |
|---|---|---|---|
| TallyPrime native WhatsApp | Send invoice / reminder from inside Tally | ₹1,300-1,800/yr + top-ups | Manual per invoice, outbound only |
| Tally TDL add-ons | Auto reminder by ageing | One-time, from ~₹2,999 | Reminders only, no replies, no payment loop |
| CredFlow | Tally/BUSY sync, multichannel reminders | ~$11-28/month (third-party listing) | Sync failures, weak support, drifting to lending |
| BUSY built-in | Manual reminder; paid automation API | Unlisted | Manual; automation sold by third parties |
| Vyapar | Billing + reminders for small retail | ~₹3,400-4,000/yr | Not built for wholesale credit |
| Khatabook / OkCredit / Biz Analyst | Ledgers | Free / coins | Moved into lending |

Conclusion: never sell "reminders". Sell recovered money.

## Build order

Each step gets its own implementation plan. Step 6 waits for step 2.

0. Server back up on Google Cloud
1. Plans and limits
2. Five tabs + setup wizard
3. AI replies
4. Recovery file + advocate partner
5. Non-Tally shops: WhatsApp-forward, then BUSY reader
6. Videos

---

## 0. Server on Google Cloud

Follow `deploy/GCP_RUNBOOK.md`: e2-medium in asia-south1, Ubuntu 24.04,
restored from the state bundle with `bootstrap.sh`. The founder does the
signup, billing (INR) and card. Kamatera stays powered off while GCP is live.
Done when both `/health` URLs return 200, the bot is connected without a new QR
scan, a test reminder arrives, and a backup verifies.

## 1. Plans and limits

| Plan | DB value | Price/month | Active debtors | Messages/month | Daily cap | Features |
|---|---|---|---|---|---|---|
| Lite | `starter` | ₹699 | 150 | 1,200 | 50 | Everything in Collect |
| Collect | `growth` | ₹999 | 400 | 3,200 | 100 | Bill on WhatsApp, reminders, batches, pay link, payment reconciliation to Tally |
| Recover | `pro` | ₹1,499 | 1,000 | 8,000 | 150 | Collect + AI replies, promise follow-up, Recovery file, multi-company |
| Custom | `max` | quoted | 5,000 | 40,000 | 300 | Recover, priced per shop |

- Lite and Collect have identical features; only the debtor count differs
  ("same features, more customers"). Recover is the only feature step up.
- Keep the existing Postgres enum values; only labels, prices, limits and
  feature flags change. No enum migration.
- Feature flags in `PLAN_LIMITS` (app/models.py): `ai_replies`, `recovery_file`,
  `multi_company` are 1 only for `pro` and `max`. The current `bot` flag stays
  as is (owner bot on every plan that has it today).
- Annual price = 10x monthly (2 months free).
- Existing shops: the founder reviews a list mapping every shop to
  `recommend_plan(active_debtors)` before it is applied. A shop already paid up
  keeps its current limits until `plan_expires_on`.
- Billing stays on the current renewal flow (manual UPI + founder marks paid).
  UPI AutoPay is out of scope for this step.
- The website pricing page and the in-app plan screen show exactly these three
  plans plus "Custom: talk to us".
- Replaces the July "annual ₹5,999-6,999 flat" decision.

## 2. Five tabs + setup wizard

| Tab | Route | Holds | Comes from |
|---|---|---|---|
| Today (home) | `/admin/today` | Money expected, who to chase, needs a decision, "Going out today", "N parties owe ₹X, no number", "Recovered this month" strip | Today + Due today + Missing numbers + headline of Analytics |
| Payments | `/admin/payments` | Promises, receipts to confirm, post to Tally | Unchanged |
| Parties | `/admin` | Party list -> party page | Unchanged |
| Reminders | `/admin/reminders` | Batches (tone, language, hour, custom line, UPI) + weekly off + holidays + assign parties | Reminders + Batches merged |
| Settings | `/admin/settings` | UPI and bank, WhatsApp status, Tally status, plan, Udyam number | Settings + Accounts |

- `/admin/batches`, `/admin/accounts`, `/admin/analytics` redirect to their new
  home so old links and desktop bookmarks keep working.
- Analytics tab removed. A monthly WhatsApp report to the owner (1st of the
  month, 10:00 IST) carries recovered amount, top 5 overdue, and the ageing
  split. It reuses the EOD digest sending path.
- Catch-up stays a banner, letter preview stays a pop-up, reconcile and
  notifications stay invisible.
- Desktop app tab bar (desktop/main.js) shows the same five tabs.
- Setup wizard (`/admin/setup`), first run, one step per screen, resumable:
  1. Scan WhatsApp QR (shop number), with a live "connected" tick
  2. Connect Tally (pairing code, company pick)
  3. UPI ID (and optional bank)
  4. Import phone contacts (existing vCard / Google CSV / paste import)
  5. Pick a starter batch and send the first reminder preview to the owner
- Done when a new shop can go from install to first reminder without opening
  any other tab.

## 3. AI replies (Recover plan)

Applies to customer messages arriving on the shop number (`wa_service` 3001).
This reverses the 1.8.10 rule "the shop number never replies to customers",
for these cases only:

| Customer message | ASVA does |
|---|---|
| Asks balance, bill copy, statement, or how to pay | Auto-replies with the fact (amount, PDF, statement, pay link) in the customer's language |
| Promise ("5 tareek ko"), paid claim, extension request, anything else readable | Pauses reminders as today, drafts a reply, sends the owner "Reply 1 to send / 2 to write your own" on the bot number |
| Dispute ("bill galat hai"), anger, unclear, low confidence | Nothing to the customer. Owner is alerted, as today |

- Drafts expire unsent after 24 hours.
- At most 3 auto-replies per customer per day; above that, owner decides.
- Per-shop switch "Auto-answer factual questions" in Settings, default on for
  Recover.
- Every auto-reply and every approved draft is logged in `messages` with its
  own message type.
- Model: Gemini Flash-Lite through the existing `intent.py` REST pattern. Cost
  is about ₹0.02 per reply; log tokens per shop to confirm.
- Lite and Collect keep today's behaviour (silent capture, owner nudged).
- No em or en dashes in any customer-facing reply, any language.

## 4. Recovery file + advocate partner (Recover plan)

- One tap on the party page (and bot command `FILE <name>`) builds a PDF:
  party details, statement of account, open bills, payments received, every
  reminder with sent and delivered time, and promises made and broken.
- If the shop has a Udyam number in Settings: a factual section on MSMED Act
  s.15 (payment within agreed time, max 45 days; 15 days without agreement),
  s.16 (compound interest at 3x RBI bank rate) and Income Tax s.43B(h), plus a
  Samadhaan-ready summary. Without Udyam the section is left out.
- ASVA never sends a legal threat itself. The owner downloads or shares the
  file.
- "Send to advocate" button: a paid per-notice add-on through a partner
  advocate. Hidden until a partner is signed; price set then.
- Gate: a lawyer reviews every legal sentence in the PDF before this ships.

## 5. Non-Tally shops

Step 5a, WhatsApp-forward (any accounting software, or paper):
- The owner forwards the outstanding report to the ASVA bot as a PDF, photo or
  screenshot.
- Gemini reads it into rows (party, amount, oldest bill date, phone if shown)
  using the `ocr.py` pattern.
- The bot replies "Found 142 parties, ₹38,20,000. Reply YES to load".
  YES loads it through the existing outstanding import path; NO discards it.
- Every Monday 10:00 the bot asks the owner for a fresh report.
- Payments confirmed in ASVA stay in ASVA (no write-back for non-Tally shops).

Step 5b, BUSY direct reader: starts when `busy_agent/discover.py` has been run
on a real BUSY install. Read side first, per `busy_agent/source_contract.py`.

Excel upload is not the path for non-Tally shops. The existing numbers sheet
import stays.

## 6. Videos (after step 2)

- 60-second pitch: "your Saturday collector, every day, no salary".
- 5-minute setup: the wizard from install to first reminder.
- 2-minute daily use: open Today, confirm a payment, see it in Tally.

## Out of scope

Payment gateways (ASVA never routes money), AI voice calls, credit bureau or
credit-risk gating, UPI AutoPay, Marg/Focus direct readers.
