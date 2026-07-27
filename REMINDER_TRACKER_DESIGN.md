# Reminder tracker — design (build later)

Status: **design-only** (2026-07). Approved to design, not yet to build.

## The problem it solves

Today the owner can see a party's dues and next reminder, but not the **story**:
"I reminded him 3 times, he replied once saying he'd pay, then went quiet." The
tracker turns each party into a short, honest timeline so the owner knows exactly
where a chase stands — and, crucially, sees **"he did not reply to that."**

## What we already have (no new storage)

- **Reminders sent**: the `messages` table already records every reminder
  (`type='reminder'`, `client_id`, `reminder_day`, `created_at`, `delivery_status`).
- **Promises / paid-claims**: `payment_promises` stores the reply that mattered
  (`raw_text`, `promise_date`, `status` = open/kept/broken/superseded).
- So a v1 timeline can be built **from existing data**: reminder events + promise
  events, interleaved by time.

## What we're missing (the one new piece)

- **Full inbound message text**, linked to the party. Right now we store only the
  inbound message *id* for dedup, plus the promise `raw_text`. A "did he reply?"
  needs to know a reply arrived even when it wasn't a promise (e.g. "kal baat
  karte hain"). → add a small `inbound_messages` store: `business_id, client_id,
  text, received_at` (retain ~90 days). Written in `replies.capture_reply`.

## The timeline (per party)

Newest first, on the party page and in the mobile app:

```
● 12 Aug  Reminder sent (day 30)              delivered
● 12 Aug  Customer replied: "10 tareek ko dunga"   → promise, paused to 10 Aug
● 05 Aug  Reminder sent (day 23)              delivered   ⚠ no reply
● 28 Jul  Reminder sent (day 15)              delivered   ⚠ no reply
```

- **"⚠ no reply"** = a reminder with no inbound message from that party within N
  days after it. This is the "he did not reply to that" flag you asked for.
- A **promise** event shows the customer's own words and what it did (paused to X).
- A **broken promise** event shows in red ("promised 10 Aug, still unpaid").

## Roll-up signals (for who-to-chase priority)

- **Silent**: reminded ≥2 times, zero replies ever → likely needs a call, not another WhatsApp.
- **Talks but doesn't pay**: replies/promises but promise broken ≥1 → escalate.
- **Responsive**: replied recently / promise still open → leave alone.

These become sort/priority hints in the mobile "who to chase" list.

## The "reply from a different number" gap

When an inbound number matches no party (via `phones.same_number`), we currently
ignore it. Design: keep a small **"unmatched replies"** tray for the owner —
"a message came from +91 98xxx, not linked to any party. Link it to: [party
picker]." Linking writes that number onto the party (or a secondary-numbers
list), so future replies attribute correctly. This is opt-in and owner-driven, so
we never guess wrong.

## Build order when we do it

1. `inbound_messages` store + write it in `replies.capture_reply` (small).
2. Timeline builder (pure function over messages + promises + inbound).
3. Party-page timeline UI (desktop) + mobile timeline.
4. Roll-up signals into the chase priority.
5. Unmatched-replies tray + "link number to party".

Each step ships independently and is testable in isolation.
