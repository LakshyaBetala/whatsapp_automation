-- =====================================================================
-- 007 — Per-business reminder cadence
--
-- Cadence days count from the INVOICE date (gentle nudges), then once a
-- bill passes its due date the overdue track repeats every
-- overdue_repeat_days, overdue_max_repeats times, and finally escalates
-- to the owner ("call them yourself"). Clients with long credit terms
-- (> 30 days) skip the nudges — they get one courtesy heads-up before
-- due, then the overdue track.
-- =====================================================================

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS reminder_cadence    int[] NOT NULL DEFAULT '{3,7,15,21,30}',
  ADD COLUMN IF NOT EXISTS overdue_repeat_days int   NOT NULL DEFAULT 7,
  ADD COLUMN IF NOT EXISTS overdue_max_repeats int   NOT NULL DEFAULT 3;
