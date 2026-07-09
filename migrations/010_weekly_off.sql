-- 010: per-business weekly off day.
-- Python weekday convention: Monday=0 .. Sunday=6; NULL = open 7 days.
-- Default 6 (Sunday) so reminders skip Sundays unless the shop opts out.
-- Festival/holiday dates continue to use businesses.blackout_dates (date[]).

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS weekly_off_day smallint DEFAULT 6;
