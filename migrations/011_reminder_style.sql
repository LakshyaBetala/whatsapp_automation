-- 011: owner-facing reminder controls (Slice A - scheduling brain).
--   reminder_style       gentle | standard | firm  (sets tone; drives cadence)
--   reminder_custom_line one optional line appended to every reminder
--   reminder_hour        hour of day (0..23) reminders go out; the hourly sweep
--                        sends at this hour, or the next hour the host is on.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS reminder_style       text     DEFAULT 'standard',
  ADD COLUMN IF NOT EXISTS reminder_custom_line text,
  ADD COLUMN IF NOT EXISTS reminder_hour        smallint DEFAULT 11;
