-- 016_reminder_batches.sql
-- Reminder batches: up to 5 named batches per business, each with its own
-- severity (style), language, early-pay discount and custom line. Parties are
-- assigned to a batch; the reminder engine + Send Now use the party's batch
-- instead of one global setting. Batch 0 = the default (first) batch.
alter table businesses
  add column if not exists reminder_batches jsonb not null default '[]'::jsonb;

alter table clients
  add column if not exists reminder_batch smallint not null default 0;
