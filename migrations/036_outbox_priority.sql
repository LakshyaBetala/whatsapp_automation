-- 036_outbox_priority.sql
-- Owner-initiated sends (Send Now, and a freshly-made bill the owner just
-- created) must go out immediately, even after the customer send window (9-19).
-- Automated reminders stay inside the window. A 'priority' row bypasses the
-- window in outbox.pull / outbox_sweep.
alter table wa_outbox
  add column if not exists priority boolean not null default false;
