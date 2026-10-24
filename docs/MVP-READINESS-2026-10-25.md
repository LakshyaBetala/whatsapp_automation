# ASVA MVP readiness audit

**Date:** 25 October 2026
**Question:** is this an MVP?
**Answer:** not yet, and the gap is narrow and specific.

---

## The verdict

The core loop is built and it demonstrably works. RISHAB has had **1,920
messages delivered** through Tally sync, reminder, reply handling and receipt
write-back. The product is real.

But an MVP is not "the features exist". It is **a stranger can install it and it
keeps working for thirty days without the founder touching it.** That bar is not
met: six of the first twelve installs stopped contacting the server within four
days, and three never contacted it at all.

> **ASVA is a working product sitting on a broken delivery substrate.**

The features are not the problem. The substrate is. Everything below is sorted
by that.

---

## Why WhatsApp disconnects

Two different failures get reported as one, and the logs make them look
identical.

### Failure 1: the bot has no session at all (live right now)

On **20 October at 14:58** the bot number received a `code=401` (logged out from
the phone). The handler did the right thing and wiped the session. Since then:

```
session-bot/            0 files
registered              false
"QR expired unscanned"  310 times in 24 hours
code=408                925 times in 7 days
last successful CONNECT 24 Oct 05:55  (~15 hours ago)
```

It is not disconnecting. **It has never been logged back in.** It generates a QR,
nobody scans it, the QR expires after ~2m43s, it generates another. For five
days.

**The log is actively misleading.** Each cycle prints
`Connection closed (code=408)` before `QR expired unscanned`, so a week of
"nothing is wrong, nobody has scanned" reads as 925 connection failures. Anyone
reading that log concludes the network is broken.

**Fix:** scan the QR at `link.tryasva.com/qr`. Then stop the log from crying
wolf (P0-2 below).

### Failure 2: a real session dropping (RISHAB, the paying shop)

Three `wa_down` criticals in two days (23rd 20:05, 24th 02:34, 24th 04:59), each
emailed, each self-resolved. This is a genuine registered session dropping and
coming back.

The cause is structural, not a bug: **RISHAB runs on laptop transport.** The
WhatsApp session lives on the shop's own Windows machine, so it dies every time
that machine sleeps, is shut down, changes network, or is updated. The code
handles it correctly - transient drops reconnect with no re-scan - but every
drop is a window where nothing sends, and the queue ages toward expiry.

This is the same root cause as SUPREME's **313 messages queued, 10 delivered,
300 expired**.

**Fix:** the session belongs on a server that does not sleep. Which is the whole
point of the next section.

### The disconnect codes, and what each actually means

| Code | Meaning | Handled? |
|---|---|---|
| 515 `restartRequired` | normal, straight after a scan | yes, restarts in 400ms |
| 401 `loggedOut` | unlinked from the phone, or phone offline 14+ days | yes, wipes and shows a fresh QR |
| 500 `badSession` | usually a transient stream error | yes, retries before wiping, then tries the backup |
| 440 `connectionReplaced` | a second process grabbed the session | yes, warns and backs off 60s |
| 403 `forbidden` | **WhatsApp has blocked the number** | **no - see P0-1** |
| 408 `timedOut` | socket timeout, or an unscanned QR expiring | falls through to generic backoff |

---

## P0 - blocks MVP

### P0-1. A ban is hammered instead of reported
`wa_service/index.js:589`. On 403 the code logs "Re-scanning will not help" and
then calls `scheduleRestart(300000)` - reconnecting into the block every five
minutes, forever, with nobody told. Hammering a block is how a temporary one
becomes permanent, and a banned number is the single worst outcome ASVA can
cause a customer.
**Fix:** halt that shop, raise a critical alert, do not reconnect.

### P0-2. The log cries wolf
925 `Connection closed (code=408)` lines for a condition that is simply "nobody
scanned". Every diagnosis starts from this log; it must not lie.
**Fix:** when `!registered`, do not print a connection-closed line at all - print
"waiting for someone to scan the QR (nnn minutes)". Keep `code=` lines for real
drops of a registered session.

### P0-3. Every shop is on laptop transport
12 of 12. This is the direct cause of the expired outbox and of SUPREME's 3%
delivery rate. New shops now reserve a server port at creation and the setup
wizard drives straight to the server QR (shipped today). Existing shops need
their owner to scan once - Baileys sessions cannot be transferred.
**Fix:** one call for RISHAB; the rest as part of re-onboarding.

### P0-4. The bot has no WhatsApp session
Down since 20 October. Owner digests, alerts, replies and the trial pitch are all
dead until someone scans.
**Fix:** scan it today.

### P0-5. The dead-shop alerts are committed but not deployed
`shop_dying` and `shop_never_started` exist in `0f86d26` and do nothing until the
backend restarts.
**Fix:** deploy.

---

## P1 - blocks scale past ~20 shops

### P1-1. One Node process per shop
`manager.js:66` spawns a full Node runtime per shop - about 40 MB of the 85-106 MB
each one costs. Pool roughly ten shops per process, keeping several processes so
one crash does not take the fleet. Worth doing at ~40 shops, not before.

### P1-2. Day one looks broken
The warm-up ramp means a new 500-party shop sends 20 messages on day one and is
only fully covered on day six. Correct engineering, invisible to the owner.
**Fix:** an onboarding line and a dashboard "Day 2 of 6 - 120 of 500 reminded".
Not a higher cap.

### P1-3. No daily-cap-reached notice
When a shop hits its daily cap the remainder silently waits. The owner should be
told once per day.

### P1-4. `outbox_sweep` reports stale since 15 July
Evaluated locally it raises a critical "job stopped running". It appears to be a
local-settings artifact - production's monitor runs clean and `outbox_expiry`
from the same module is healthy - but it is unproven, and if production ever
enables `ENABLE_OUTBOX_SEND` it will emit a permanent false critical into the
inbox the dead-shop alerts now depend on.

### P1-5. Setup wizard is in Hinglish
`admin_setup` is written in Hinglish ("Aap kis line me ho?", "chhote step, phir
aap live") regardless of the owner's language choice. The standing rule is
English everywhere unless the owner picked Hinglish. The first screen a new shop
sees breaks it.

---

## P2 - quality

- The laptop banner on Today is deliberately quiet. Twelve shops ignored it.
- `wa_transport` has no operator-visible history, so a shop that silently falls
  back cannot be spotted after the fact.
- Party batch assignment takes select, then dropdown, then Apply, and the
  selection checkbox doubles as the reminders on/off toggle (ideated, not built).

---

## Tests that need to exist

1,078 tests pass today, and the parts that have been debugged in anger are well
covered. The gaps are in scheduled jobs - exactly the code that runs unattended
at 21:00 with nobody watching.

| Module | State | What the test must pin |
|---|---|---|
| `app/jobs/eod_digest.py` | **no tests** | digest fires once per shop per day, never to a suspended shop, never empty |
| `app/jobs/promise_followup.py` | **no tests** | a promise pauses the cadence, and resumes exactly once on the promised date |
| `app/jobs/reminder_checkpoint.py` | **no tests** | a half-finished sweep resumes without re-sending |
| `app/jobs/onboarding_nudge.py` | **no tests** | nudges stop once setup completes |
| `app/jobs/keepalive.py` | **no tests** | the Supabase ping actually prevents the free-tier pause |
| `app/services/reply_rules.py` | thin | every preset rule, and that an unmatched reply falls through to the AI |
| `app/services/templates.py` | thin | every template renders in both languages with no placeholder left unfilled |
| `wa_service/index.js` | **none** | each disconnect code takes its intended branch |

That last one matters most. Every disconnect decision in the product is
untested JavaScript, and a wrong branch either wipes a working session or
hammers a ban.

---

## How to prove it is MVP ready

Not by a feature list. By one test:

> **Install ASVA on a machine you do not control, for a shop you do not call,
> and have it still delivering on day 30.**

Measurable exit criteria:

| # | Criterion | Today |
|---|---|---|
| 1 | 3 shops on server transport | **0** |
| 2 | Delivery rate above 95% for 14 consecutive days | 72% (RISHAB), 3% (SUPREME) |
| 3 | Zero shops dark more than 24h without an alert reaching you | alerts built, not deployed |
| 4 | A shop onboarded with no founder intervention after the QR scan | never attempted |
| 5 | Every disconnect code has a test | none do |
| 6 | 30-day survival for at least 3 shops | 1 (RISHAB, and it flaps) |

Criterion 2 is the one that matters. **G AND G hit 97% across 231 messages and
stayed 52 days** - the longest of any non-paying shop. One data point, but it is
the only shop where the product worked properly, and it is also the one that
stayed. Reliability is the retention strategy.

---

## Order of work

1. Scan the bot QR. (minutes)
2. Deploy `0f86d26` so the alerts are live. (minutes)
3. Fix the 403 halt and the misleading 408 log. (hours)
4. Move RISHAB to server transport. (one call)
5. Write the five missing job tests. (a day)
6. Re-onboard the dead shops onto server transport, and watch criterion 2.

Steps 1 to 4 are a day's work and move four of the six criteria. Do not build
anything new until criterion 2 holds.
