-- 057_bill_notices.sql
-- A new bill reaches the customer on WhatsApp 5-10 minutes after Tally syncs
-- it, without anyone exporting a PDF. Bills a customer gets in quick
-- succession are merged into one message (10-minute window, at most 30
-- minutes after the first), at most one bill message per customer per 30
-- minutes, and nothing goes out 22:00-08:00 IST (it waits for 08:00).
create table if not exists public.bill_notices (
  id            uuid primary key default gen_random_uuid(),
  business_id   uuid not null references public.businesses(id) on delete cascade,
  client_id     uuid not null references public.clients(id) on delete cascade,
  bill_id       uuid not null references public.bills(id) on delete cascade unique,
  first_at      timestamptz not null default now(),
  send_after    timestamptz not null,
  status        text not null default 'pending'
                  check (status in ('pending', 'sent', 'skipped')),
  sent_at       timestamptz,
  message_db_id uuid,
  note          text,
  created_at    timestamptz not null default now()
);

create index if not exists idx_bill_notices_due
  on public.bill_notices (send_after) where status = 'pending';
create index if not exists idx_bill_notices_client
  on public.bill_notices (business_id, client_id, status);

alter table public.bill_notices enable row level security;

-- Per shop on/off (default on). The owner can switch it off in Reminders.
alter table public.businesses
  add column if not exists bill_notices boolean not null default true;
