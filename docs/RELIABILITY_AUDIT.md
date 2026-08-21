# ASVA reliability audit (2026-08-21)

Goal: every feature is *true* (never claims something it did not do) and *robust*
(survives a shop laptop being off, a flaky network, a WhatsApp drop, a bad photo,
a poison queue row, a renamed Tally party). Status per feature below, with the
concrete failure mode and what now guards it. Fixes marked ✅ are done this pass.

Legend: ✅ fixed/robust · 🟡 acceptable, watch · 🔭 recommended next.

---

## 1. EOD digest / "reminders sent today" — was LYING ✅
**Failure:** the digest counted a reminder as "sent" whenever a `messages` row
existed with a `sent_at` in today's range. `messages.sent_at` is a DB default
(row-creation time) and is stamped even for **queued** and **FAILED** sends, and
the count had **no `delivery_status` filter**. So a reminder that failed because
the customer's number is not on WhatsApp, or because the shop's WhatsApp was
disconnected, or that is still sitting queued, was reported to the owner as
"reminded". This is exactly the "it says it reminded a client whose WhatsApp was
never connected" report.
**Fix:** the count now gates on `delivery_status in (sent, delivered, read)`
([`eod_digest.py`](../app/jobs/eod_digest.py)). The health dashboard already used
the same set (`monitoring.py`), so the two now agree.
**Verified:** the reminder sweep already skips clients with no `whatsapp_number`,
so the only inflation source was failed/queued sends — now excluded.

## 2. WhatsApp disconnects too often ✅ (one real cause fixed) / 🟡
Reviewed `wa_service/index.js` against the Baileys connection-lifecycle guidance.
The reconnect handling is already strong and, on one point, better than the docs:
- 515 restartRequired → immediate restart; 401 loggedOut → wipe + fresh QR;
  440 connectionReplaced → slow retry + loud warning; 403 forbidden → long backoff;
  unscanned-QR expiry → fast restart; transient drop → capped exponential backoff.
- 500 badSession → reconnect with the SAME creds a few times, then restore the
  last-good backup, and only wipe as a last resort (the docs say wipe immediately;
  wiping an unattended shop's working login drops it into a QR loop nobody scans,
  so we deliberately don't).
- `getMessage` + `msgRetryCounterCache` + `retryRequestDelayMs`/`maxMsgRetryCount`
  stop the decrypt-retry loop that caused the code-500 storm.
- `markOnlineOnConnect:false`, `keepAliveIntervalMs:25000`, torn-write backup/
  restore so an auto-update kill doesn't force a re-scan.

**Real cause fixed this pass:** `start()` (the Baileys connect) ran *before*
`app.listen(PORT)`, so a **second** wa_service (e.g. an auto-update race that left
the old one alive) would connect a second socket on the same session → a **440
"connection replaced" that knocks the live one offline**. Now the port is the
single-instance guard: `start()` runs only after `listen` succeeds, and a second
instance hits `EADDRINUSE` and exits **without ever touching WhatsApp**. (Ships in
the next shop exe.)
🔭 Remaining: an external uptime check per shop WhatsApp, and surfacing "last
connected" age to the owner. The library is current (`fetchLatestBaileysVersion`).

## 3. Outbox delivery / "N messages stuck in queue" ✅
**Failure (earlier this session):** one poison row retried forever blocked every
newer reminder (head-of-line), and stale rows only expired while a shop was
online, so days-offline built a backlog that would blast on reconnect.
**Fix:** drainer continues past per-message failures; a server-side
`expire_stale()` sweep caps the queue at `EXPIRE_HOURS` on the VPS every 15 min
regardless of shop connectivity. RISHAB's 33 stuck rows were cleared.

## 4. Payment capture / promise-to-pay ✅
**Failure:** any short-caption image was treated as a payment screenshot → paused
the debtor and could queue a hallucinated amount from a product photo/selfie/bill.
**Fix:** OCR classifies first (`is_payment`); non-payments are shown to the owner
with reminders left running and nothing queued; a real payment with an unclear
amount is surfaced to confirm, never guessed.
**Reviewed, already robust:** promise reconciliation marks a promise *kept* only
when Tally has cleared the bills, else resumes reminders (fail-safe on any error);
dormant shops skipped; `followup_sent_at` blocks double-processing. Receipt dedup
(one open receipt per party) prevents double-posting; FIFO allocation is exact.

## 5. Tally integration ✅ / 🟡
- New-party auto-create failed with a `credit_days` NOT-NULL violation that
  stopped ALL new parties for a shop — fixed on every insert path + a DB default
  of 30 (migration 047).
- Renames are GUID-resolved so a renamed party is not duplicated; sales dedup on
  `tally_voucher_number`; receipts dedup via `tally_receipts`; OB seeded from FY
  opening. 🟡 Watch: a party moved out of Sundry Debtors is intentionally ignored.

## 6. Inbound webhook / owner commands ✅
P0 impersonation hole closed (bot channel requires the shared secret; shop channel
proves itself with its agent token). Rate-limited; always-200; dedup on message id.

## 7. Onboarding ✅
Welcome on first sync + nudge for paired-but-unsynced shops (both once, owner-only,
degrade silently if unmigrated). `credit_days` default keeps new shops from erroring.

---

## Definition of "error-free" we are converging on
1. No feature reports an action it did not complete (digest ✅, health ✅).
2. No single bad input (photo, poison queue row, renamed party, missing field)
   breaks a batch or a shop (✅ across capture, outbox, Tally, credit_days).
3. WhatsApp survives updates, sleep, and net blips without a re-scan, and a
   duplicate process can never disconnect the live one (✅).
4. Every swallowed error lands in the Command Center (errorlog + monitor ✅).

## Still open (prioritised)
- 🔭 Per-shop WhatsApp uptime monitor + "last connected" shown to the owner.
- 🔭 Escalation ladder (day-based, promise-aware) + credit-limit control — the two
  capabilities competitors (Kenso) rank above us; also the biggest product wins.
- 🔭 Move admin/ops token out of URLs; Cloudflare WAF; `pip-audit` in CI; rotate
  historically-exposed credentials (see `PRODUCTION_HARDENING.md`).
