# ASVA reliability and smart replies (design)

Date: 2026-10-20. Status: approved in brainstorming, awaiting spec review.
Evidence: `docs/AUDIT_2026-10-20.md`. Builds on
`2026-10-20-asva-simplify-and-reposition-design.md` (plans, 5 tabs, Recovery
file), which stays valid.

## Decisions (founder, 20 Oct 2026)

| Topic | Decision |
|---|---|
| Shop WhatsApp | Runs on the server, not the shop laptop |
| Customer auto-replies | Balance / statement asks, bill copy / how to pay, paid-claim acknowledgement. Nothing else. |
| Promises | Pause reminders and tell the owner; no reply to the customer |
| AI provider | Rules first, Gemini 2.5 Flash-Lite on Vertex AI (trial credit), AI Studio free key as fallback |
| Renewal | Manual UPI stays, with stronger reminders and one-tap renew for the founder |
| New bills | Sent 5-10 minutes after sync, merged per customer, quiet hours, daily cap |
| Server | GCP e2-medium (4 GB) during the trial; no 6 GB; Kamatera around day 80 |

## Order

1. Stop the bleeding
2. Shop WhatsApp on the server + desktop 2.2.0
3. Smart replies and the smart owner bot
4. Bills on creation and pay-link insight
5. Renewal reminders and one-tap renew
6. Website and guide
7. Move to Kamatera

Each step gets its own implementation plan and ships on its own.

---

## Step 1. Stop the bleeding

### 1.1 Cadence markers tell the truth

- Migration `054_marker_parent.sql`: `messages.parent_message_id uuid null`,
  indexed.
- `reminder_sweep`: when `_send_consolidated_reminder` returns `queued`, write
  each marker with `delivery_status='queued'` and `parent_message_id` = the real
  message's `messages.id`. When it returns `sent`, write `sent` as today.
- Outbox delivery (`outbox` service, where a row turns `sent`): update markers
  with that `parent_message_id` to `sent`.
- Outbox expiry or failure: delete markers with that `parent_message_id`, so the
  cadence point is eligible again at the next sweep (the daily cap still
  applies).
- `_already_sent` treats `queued` and `sent` markers as taken (no re-queue while
  one is pending).
- One-off repair in the same migration: delete `cadence_marker` rows whose real
  message (same client, created within 5 s, not a marker) is `expired` or
  `failed`. 894 rows, 380 customers today; the daily caps spread the resends.
- Every "reminders sent" count excludes `template_name='cadence_marker'`:
  `monitoring.snapshot` traffic and `sent_today`, Command Center, Today, the
  owner digest's "sent today", analytics, `_parties_sent_today` keeps reading
  markers (it needs them) but counts only `queued`+`sent`.
- Test: queue -> expire -> marker gone -> next sweep re-queues; queue ->
  deliver -> marker `sent`; counts exclude markers.

### 1.2 Reply understanding

- `intent.py`: model `gemini-2.5-flash-lite`, `thinkingConfig.thinkingBudget = 0`.
- Intents: `paid_claim`, `promise`, `dispute`, `balance_query`,
  `statement_request`, `bill_request`, `payment_info`, `order`,
  `auto_greeting`, `chatter`, `unclear`.
- Prompt covers English, Hindi, Hinglish, **Tamil and Tanglish**, Gujarati,
  Marathi, with examples: "Nill balance", "not ours pls check" = dispute;
  "kitna baki hai" = balance_query; "10 box bhejo" = order.
- Rules before the model (no AI call): `PAID...`, `STOP`, business auto-greetings
  (starts with "Welcome to", "Thank you for contacting", "Thanks for reaching
  out", or a `*Bold Name*` greeting with "how can we help").
- Routing in step 1 (before auto-replies exist): dispute -> owner alert, no pause
  (today "Nill balance" wrongly pauses); balance / statement / bill /
  payment_info -> owner nudge "X asked for ..."; order, auto_greeting, chatter ->
  stored, no action.
- `conversations.record` stores the final intent (classify first, then write;
  or update the row after classification).
- Tests: the four live cases from the audit, the greeting filter, intent stored.

### 1.3 Private invoices

- `invoices` storage bucket set to private. Wherever a stored PDF URL is shown
  (dashboard, party page), serve a signed URL valid 7 days. Customer sends keep
  using base64 (unchanged).

### 1.4 Outage alarm and backups

- External uptime monitor: the founder creates a free UptimeRobot account and
  adds `https://app.tryasva.com/health` (expect 200) and
  `https://link.tryasva.com/api/wa/status` (expect 401), every 5 minutes, alerts
  by email and the UptimeRobot phone app. Documented in `deploy/VPS_MIGRATION.md`.
- Off-box backups: a laptop script `deploy/pull_backups.ps1` copies the newest
  nightly dump from `asva-vps` over Tailscale, keeps the last 14, and runs daily
  from Windows Task Scheduler when the laptop is on. Server keeps 7.

### 1.5 Plans and website copy

- `PLAN_LIMITS`: Lite `starter` Rs 699 / 150 debtors / 1,200 messages / daily 50;
  Collect `growth` Rs 999 / 400 / 3,200 / 100; Recover `pro` Rs 1,499 / 1,000 /
  8,000 / 150; Custom `max` unchanged. Labels shown as Lite / Collect / Recover.
- Existing shops keep their limits until `plan_expires_on`; the founder reviews
  a mapping list before any shop's plan changes.
- Website: remove every "free till 30 September" line in `app/site.py`; pricing
  page shows the three plans and "Custom: talk to us"; guide stops promising a
  PDF with every bill and lists Tamil.

### 1.6 Small server fixes

- `asva-bot.service` runs `node index.js` (not `npm start`): about 53 MB back.

Done when: tests pass; markers for an expired message are gone; Health shows
real counts; the four audit messages classify correctly in under 1.5 s; the
bucket is private; the website shows prices; deployed with `push_release.sh`.

---

## Step 2. Shop WhatsApp on the server

### 2.1 One WhatsApp service, many shops

- `wa_service` gains `WA_MODE=multi`: one Node process, one Baileys socket per
  business, auth under `.baileys_auth/<business_id>/`.
- Session files are encrypted at rest (AES-256-GCM, key `WA_SESSION_KEY` in
  `/etc/asva-bot.env`, generated by bootstrap) through a custom auth-state
  wrapper. The state bundle carries the key.
- Local-only API (127.0.0.1, `x-asva-key`): `POST /api/wa/:biz/send`,
  `GET /api/wa/:biz/status`, `GET /api/wa/:biz/qr`, `POST /api/wa/:biz/relink`,
  `POST /api/wa/:biz/logout`, `GET /api/wa/sessions`.
- Inbound messages forward to `/webhooks/aisensy` with `business_id` and
  `channel=shop`; messages the owner types on the phone (`fromMe`) forward as
  `human_reply` events so step 3 can stay quiet.
- Per-session reconnect keeps today's rules (backoff, no wipe except
  `loggedOut`); systemd restarts the process; memory alert at 75%.
- RAM budget: about 20-40 MB per shop. 4 GB covers well past 50 shops.

### 2.2 Who sends: the `transport` switch

- Migration `055_wa_transport.sql`: `businesses.wa_transport text not null
  default 'laptop' check (wa_transport in ('laptop','server'))`.
- `server` shops: the server's outbox sweep drains their `wa_outbox` rows
  through the multi service; `wa_ready` / `wa_checked_at` come from the server.
- The laptop claim endpoint returns nothing for a `server` shop, so any old
  desktop (2.1.3 and older) can never send for it. No double sends.
- Duplicate inbound (old desktop still linked) is already dropped by the
  message-id dedup in the webhook.
- Moving a shop: owner scans the server QR in the app -> session connects ->
  `wa_transport='server'` is set automatically -> the laptop stops sending.

### 2.3 Ban guardrails (all server shops)

- Random 20-60 s gap between sends per number (exists); at most 30 per hour.
- Daily cap from the plan (50 / 100 / 150).
- Warm-up for a newly linked number: day N after linking allows
  `min(plan cap, 20 + 10 x N)` for the first 14 days.
- Only saved customers with an outstanding receive reminders (exists).
- Two or three phrasings per reminder template per language, picked at random.
- A party with 3 reminders and no reply, no receipt and no pay-link tap in 30
  days moves to half the cadence.
- Connection closed with `forbidden` (403) or `loggedOut`: stop that shop's
  sends, mark it, alert the owner and the founder at once.

### 2.4 Desktop 2.2.0 (thin client)

- Starts its local WhatsApp only if the server says `wa_transport='laptop'`.
- The five tabs and setup wizard from the simplify spec; wizard step 1 shows the
  server QR (fetched with the agent token) and a live "connected" tick.
- Shows the new plan names and limits.
- Auth: the token in the URL is exchanged once at `/admin/session` for an
  HttpOnly cookie; old `?token=` links keep working for 2.1.x.
- Rate limiting on `/pay/*`, pairing-code redemption and `/tally/*` (in-memory
  token bucket per IP and per token; 429 when exceeded).
- Update-loader checklist applies (splash, durable notification, welcome-back,
  `autoInstallOnAppQuit=false`, full-screen overlay); publish as `.partial`,
  verify sha512, rename, `latest.yml` last, no blockmap.

Done when: Rishab runs on `server` transport with the laptop off and reminders
still go out; an old 2.1.3 desktop on a `server` shop sends nothing; 2.2.0 is on
the feed.

---

## Step 3. Smart replies and the smart owner bot

### 3.1 AI provider

- `app/services/llm.py`: one `generate_json(prompt, schema, media=None)` used by
  `intent.py` and `ocr.py`.
- Order: Vertex AI (`gemini-2.5-flash-lite`, service account key at
  `/etc/asva-vertex.json`, project `project-78637d1a-0109-49a6-96e`) -> AI Studio
  key -> `None`. `None` always means: do nothing to the customer, tell the owner.
- Token use is logged per shop per day.

### 3.2 Customer auto-replies (Recover plan only)

Answer automatically only when all hold: the sender matches a customer with an
outstanding; intent is `balance_query`, `statement_request`, `bill_request`,
`payment_info` with confidence >= 0.85, or a `paid_claim` (keyword, screenshot
classified as payment, or confidence >= 0.85); no `human_reply` in that chat in
the last 6 hours; fewer than 3 auto-replies to that customer today; not between
22:00 and 08:00 IST (then it waits until 08:00).

| Intent | Reply (facts from the database, fixed wording per language) |
|---|---|
| balance_query | Outstanding total, oldest bill date, pay link |
| statement_request | Statement of open bills and recent payments, pay link |
| bill_request | Latest bill notice (PDF when stored), pay link |
| payment_info | Pay link with UPI ID and bank details |
| paid_claim | "Thank you, we will confirm once it reaches us." Payment goes to the Payments tab; nothing is marked paid |

- Languages: English, Hinglish, Tamil, Gujarati, Marathi; the customer's stored
  language, else the language they wrote in. No em or en dashes.
- Everything else is never answered. Promise, dispute, unclear: the owner gets
  the customer's words, what ASVA did, and an AI-drafted reply in the
  customer's language: "Reply 1 to send this, 2 to ignore, or type your own."
  Option 1 or a typed reply is sent from the shop number. Drafts expire in 24 h.
- Lite and Collect: no customer replies; the owner is nudged as in step 1.
- Every auto-reply and approved draft is stored in `messages` with its own type.

### 3.3 Owner bot

- Plain text and voice notes (Gemini reads the audio) map to the existing
  commands (LIST, PAID, STOP, START, CHASE, BILL, REMIND, DIGEST, SENT, CHECK,
  RECOVERED, HELP) with their arguments; confidence >= 0.8 runs it, otherwise a
  numbered menu of the three likeliest actions.
- Typed commands keep working exactly as today.
- Answers end with at most three numbered next actions.

Done when: the audit's messages get the right reply or silence on a test shop;
an owner voice note "Ramesh ka kitna baki hai" returns Ramesh's balance.

---

## Step 4. Bills on creation and pay-link insight

- Every new sales bill (not an opening balance, invoice date within 35 days,
  customer has a number) creates a pending bill notice with
  `send_after = now + 10 min`.
- Merge window: another bill for the same customer before sending moves
  `send_after` to that bill + 10 min, never later than the first bill + 30 min;
  one message lists all of them.
- Quiet hours 22:00-08:00 IST: `send_after` becomes 08:00.
- At most 3 bill messages per customer per day; more are folded into the next.
- Message: invoice number(s), date, amount, due date, total, pay link; the PDF
  attached when the agent supplied one. Goes through the outbox, so the ban
  guardrails and the server transport apply.
- Pay link: a tap stays matchable to a Tally receipt (or confirmed payment) for
  the same party for 14 days when the receipt is at least 99% of the tapped
  amount; matched taps resolve as `tally`. Today shows "Paid through your link:
  Rs X this month"; the monthly report includes it.

Done when: a bill made at 20:00 reaches the customer by 20:10; three bills in
five minutes arrive as one message; a bill made at 23:00 goes at 08:00.

## Step 5. Renewal reminders and one-tap renew

- Owner reminders 7, 3 and 1 days before `plan_expires_on` and on the day, with
  the `/pay?plan=` link and the plan price.
- Trial end notice 3 days before and on the day.
- Command Center: "Mark renewed" per shop (1 month / 12 months) calling the
  existing renew logic; a confirmation goes to the owner.

## Step 6. Website and guide

- Separate design spec at the start of this step (design skills, visual
  companion offered). Real prices, true claims, Tamil, the setup guide matching
  the 2.2.0 wizard, then the videos.

## Step 7. Move to Kamatera

- Around 2027-01-08. Size from measured RAM (2 or 4 GB). Same bundle restore
  (`deploy/VPS_MIGRATION.md`, about 20 minutes). Vertex AI keeps working from
  any host if the Google billing account is upgraded; otherwise the AI Studio
  key takes over automatically.

## Out of scope

Payment gateway / UPI AutoPay, official WhatsApp Cloud API (pilot later), voice
calls, self-hosted Postgres, Marg/Focus readers.
