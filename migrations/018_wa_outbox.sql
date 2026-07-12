-- 018: wa_outbox - cross-laptop WhatsApp send queue.
--
-- The BOT laptop (owner assistant, its own WhatsApp number) cannot reach the
-- SHOP laptop's WhatsApp directly: the two machines share ONLY Supabase.
-- Rule: anything a PARTY (debtor) receives must come from the SMB owner's own
-- shop number, never from the bot number. So a bot-triggered customer-facing
-- send (REMIND / MSG / BILL / PAID confirmation) is queued here by the bot
-- deployment (SEND_VIA_OUTBOX=true), and the shop deployment's outbox job
-- (ENABLE_OUTBOX_SEND=true, runs every minute) delivers it from the shop
-- number with human-like pacing, then marks it sent/failed.

create table if not exists wa_outbox (
  id             uuid primary key default gen_random_uuid(),
  business_id    uuid not null references businesses(id) on delete cascade,
  message_db_id  uuid references messages(id) on delete set null,
  payload        jsonb not null,                  -- exact body for POST {shop wa}/api/wa/send
  status         text not null default 'queued',  -- queued | sent | failed
  attempts       int not null default 0,
  last_error     text,
  created_at     timestamptz not null default now(),
  sent_at        timestamptz
);

create index if not exists idx_wa_outbox_pending
  on wa_outbox(status, created_at) where status = 'queued';
