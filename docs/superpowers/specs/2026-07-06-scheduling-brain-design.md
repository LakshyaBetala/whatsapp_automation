# Slice A — The Scheduling Brain (design)

**Date:** 2026-07-06
**Status:** Approved (pilot build)
**Phase:** 1 of the "local now → cloud later" roadmap. This is the first of three
Phase-1 slices (A = scheduling brain, B = Electron shell, C = billing surface).
Slice A ships on the **current local setup** so the 7-day pilot on the shop
laptop starts immediately; the Electron packaging is a later slice.

## Goal

Give the shop owner simple, foolproof control over *when / whom / how firmly*
reminders go out, make the schedule holiday- and weekly-off-aware, and make the
whole thing survive the shop laptop being switched off for stretches (nights,
Sundays, festivals) without dropping or stacking reminders.

## What the owner sees (one "Reminder Settings" card in `/admin`)

- **Calendar** — a tap-a-day month view. Weekly-off days are shaded; tapping any
  date toggles it as a holiday (skip). Replaces the earlier dropdown + date input.
- **Weekly off** — which weekday is the shop's off day (default Sunday), reflected
  as the shaded column in the calendar.
- **Reminder style** — `Gentle / Standard / Firm`. One control that sets **both**
  the cadence (how often) **and** the wording tone.
- **Send time** — a clock picker (default 11:00). "Send at this hour, or the next
  hour the laptop is on after it."
- **Custom line** — one optional short line appended to every reminder.
- **Who gets reminders** — existing per-customer tick-box list.

Design priority: **simple, self-explanatory, impossible to misconfigure.** No raw
cadence arrays, no placeholder editing.

## Data model (migration 011)

Add to `businesses`:
- `reminder_style text default 'standard'` — gentle | standard | firm
- `reminder_custom_line text` — nullable
- `reminder_hour smallint default 11` — 0..23

Already present and reused: `weekly_off_day` (010), `blackout_dates date[]`,
`reminder_cadence int[]`, `overdue_repeat_days`, `overdue_max_repeats`.

Style → base cadence (authored for a 30-day term; the engine scales it to each
party's actual credit period):
- **Gentle** → `[7, 15, 30]`, soft wording
- **Standard** → `[3, 7, 15, 21, 30]`, neutral wording (today's default)
- **Firm** → `[2, 5, 10, 15, 20, 25, 30]`, firm wording

On save, the dashboard writes `reminder_cadence = STYLE_CADENCE[style]` and stores
`reminder_style` for tone — so the sweep keeps reading `reminder_cadence` as it
does today.

## Scheduler behaviour (the laptop-off resilience)

- The reminder sweep runs **hourly** (was daily). A business is processed when the
  **current hour ≥ its `reminder_hour`**. Per-bill dedup (bill + cadence-day) means
  each reminder still fires **at most once**, so running every hour is safe.
- **Laptop off at the send hour?** It sends the first hour the laptop *is* on after
  `reminder_hour`. **Off the whole day?** Next working day the engine sends the
  *latest un-sent* cadence point — one message, never a backlog blast (this is the
  existing "latest applicable point" logic, verified by test).
- **Weekly-off day / holiday date** → the business/bill is skipped that day. Because
  a skipped point is never marked sent, it goes out on the next working day. This is
  how "due on a holiday → send next working day" is achieved — emergently, no shift
  arithmetic.
- **Overdue scales with the term.** `cadence_points` stretches the overdue repeat
  interval proportionally to the credit period: a 30-day party keeps ~7-day overdue
  spacing; a 90-day party gets ~21-day spacing. Long-credit trades stop getting
  nagged weekly.

Everything else is unchanged: proportional cadence, overdue track, owner
escalation, daily cap, plan-limit gate, subscription gate.

## Message assembly

`render(key, lang, style=...)` picks a tone variant (`{key}_{style}`) with fallback
to the standard `{key}` then Hindi. The owner's custom line (if set) is appended
below the body, above the UPI link. Placeholders stay system-controlled.

## Testing

Unit tests (pure, no DB):
1. Holiday/off-day → next-working-day: a point due on a skipped day is sent the next
   run, exactly once, and only the latest missed point (no stacking).
2. `hour ≥ reminder_hour` + dedup: sweeping every hour of a day yields one send/bill.
3. Style → cadence mapping is applied.
4. Overdue interval scales with `due_offset` (30 vs 90 day terms differ).
5. Custom line is appended when set, absent when not.

## Explicitly out of scope (YAGNI for pilot)

- Per-*customer* send times or per-customer styles.
- Free-text template editing (tone presets + one custom line only).
- Channels other than WhatsApp.
- The Electron shell and cloud migration (later slices/phases).
