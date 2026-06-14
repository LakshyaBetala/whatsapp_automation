-- =====================================================================
-- WhatsApp Tally SaaS — initial schema
-- 6 tables: businesses, clients, bills, messages, usage, tally_syncs
-- Target: Supabase Postgres
--
-- The backend connects with the SERVICE-ROLE key and bypasses RLS.
-- RLS is enabled with NO public policies so that the anon/public key
-- can never read tenant data. Add per-tenant policies only if/when a
-- client-side dashboard is built (Phase 3+).
-- =====================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ---------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------
do $$ begin
  create type plan_tier as enum ('starter', 'growth', 'pro', 'max');
exception when duplicate_object then null; end $$;

do $$ begin
  create type lang as enum ('hi', 'gu', 'mr');   -- Hindi, Gujarati, Marathi
exception when duplicate_object then null; end $$;

do $$ begin
  create type bill_status as enum ('pending', 'partial', 'paid', 'overdue');
exception when duplicate_object then null; end $$;

do $$ begin
  create type message_type as enum (
    'invoice', 'reminder', 'payment_confirmation', 'eod_digest',
    'post_payment_pitch', 'low_stock', 'monthly_pnl', 'welcome',
    'owner_alert', 'bot_reply'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type sync_type as enum ('poll', 'eod_force', 'import', 'inventory', 'pnl');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------
-- updated_at trigger helper
-- ---------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ---------------------------------------------------------------------
-- Plan limits (single source of truth; usage checks call this)
-- ---------------------------------------------------------------------
create or replace function plan_max_clients(p plan_tier)
returns int language sql immutable as $$
  select case p
    when 'starter' then 50
    when 'growth'  then 150
    when 'pro'     then 250
    when 'max'     then 500
  end;
$$;

create or replace function plan_max_messages(p plan_tier)
returns int language sql immutable as $$
  select case p
    when 'starter' then 250
    when 'growth'  then 750
    when 'pro'     then 1250
    when 'max'     then 2500
  end;
$$;

-- =====================================================================
-- businesses — one row per SMB owner
-- =====================================================================
create table if not exists businesses (
  id                  uuid primary key default gen_random_uuid(),
  owner_name          text not null,
  business_name       text,
  whatsapp_number     text not null unique,          -- E.164, e.g. 919876543210
  tally_company_name  text,
  plan                plan_tier not null default 'starter',
  -- feature toggles
  eod_enabled         boolean not null default true,
  reminders_enabled   boolean not null default true,
  pitch_enabled       boolean not null default true,
  low_stock_enabled   boolean not null default false,
  -- festive blackout: no reminders on these dates
  blackout_dates      date[] not null default '{}',
  timezone            text not null default 'Asia/Kolkata',
  website_url         text,
  onboarding_status   text not null default 'pending',  -- pending|active|paused
  trial_ends_on       date,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create trigger trg_businesses_updated
  before update on businesses
  for each row execute function set_updated_at();

-- =====================================================================
-- clients — the SMB's customers (debtors)
-- =====================================================================
create table if not exists clients (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid not null references businesses(id) on delete cascade,
  name                text not null,
  whatsapp_number     text,                            -- E.164; null = no reminders sent
  language            lang not null default 'hi',
  credit_days         int  not null default 30
                        check (credit_days in (30, 60, 90, 120, 180)),
  reminders_enabled   boolean not null default true,
  tally_ledger_name   text,                            -- exact Tally ledger for matching
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (business_id, tally_ledger_name)
);

create index if not exists idx_clients_business on clients(business_id);
create trigger trg_clients_updated
  before update on clients
  for each row execute function set_updated_at();

-- =====================================================================
-- bills — every invoice
-- =====================================================================
create table if not exists bills (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid not null references businesses(id) on delete cascade,
  client_id           uuid not null references clients(id) on delete cascade,
  invoice_number      text,
  amount              numeric(14,2) not null check (amount >= 0),
  paid_amount         numeric(14,2) not null default 0 check (paid_amount >= 0),
  -- outstanding is always derived — never written by hand
  outstanding         numeric(14,2) generated always as (amount - paid_amount) stored,
  status              bill_status not null default 'pending',
  pdf_url             text,
  upi_link            text,
  invoice_date        date not null,
  due_date            date,                            -- invoice_date + credit period
  tally_voucher_number text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (business_id, tally_voucher_number)
);

create index if not exists idx_bills_business_status on bills(business_id, status);
create index if not exists idx_bills_client on bills(client_id);
-- partial index: the reminder sweep only ever scans unsettled bills
create index if not exists idx_bills_due_open on bills(due_date)
  where status in ('pending', 'partial', 'overdue');
create trigger trg_bills_updated
  before update on bills
  for each row execute function set_updated_at();

-- =====================================================================
-- messages — every WhatsApp sent (audit + cost ledger)
-- =====================================================================
create table if not exists messages (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid not null references businesses(id) on delete cascade,
  client_id           uuid references clients(id) on delete set null,
  bill_id             uuid references bills(id) on delete set null,
  type                message_type not null,
  reminder_day        int,                             -- 7/15/30/45/60 for reminders
  template_name       text,
  language            lang,
  aisensy_message_id  text,
  delivery_status     text not null default 'queued',  -- queued|sent|delivered|read|failed
  cost                numeric(8,4) not null default 0.145,
  sent_at             timestamptz not null default now(),
  created_at          timestamptz not null default now()
);

create index if not exists idx_messages_business_sent on messages(business_id, sent_at desc);
create index if not exists idx_messages_bill on messages(bill_id);

-- =====================================================================
-- usage — monthly message count per business (plan-limit enforcement)
-- =====================================================================
create table if not exists usage (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid not null references businesses(id) on delete cascade,
  period_month        date not null,                   -- first day of month
  message_count       int  not null default 0,
  updated_at          timestamptz not null default now(),
  unique (business_id, period_month)
);

create index if not exists idx_usage_business on usage(business_id);
create trigger trg_usage_updated
  before update on usage
  for each row execute function set_updated_at();

-- Atomic increment helper used before/after each send.
-- Returns the new count for the current month.
create or replace function increment_usage(p_business uuid, p_n int default 1)
returns int language plpgsql as $$
declare
  v_count int;
begin
  insert into usage (business_id, period_month, message_count)
  values (p_business, date_trunc('month', now())::date, p_n)
  on conflict (business_id, period_month)
  do update set message_count = usage.message_count + p_n
  returning message_count into v_count;
  return v_count;
end $$;

-- =====================================================================
-- tally_syncs — audit log of every Tally sync (debug silent failures)
-- =====================================================================
create table if not exists tally_syncs (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid not null references businesses(id) on delete cascade,
  sync_type           sync_type not null default 'poll',
  records_synced      int not null default 0,
  success             boolean not null default true,
  error               text,
  synced_at           timestamptz not null default now()
);

create index if not exists idx_tally_syncs_business on tally_syncs(business_id, synced_at desc);

-- =====================================================================
-- Lock everything down. Service-role key (backend) bypasses RLS.
-- No policies => anon/public key sees nothing.
-- =====================================================================
alter table businesses  enable row level security;
alter table clients     enable row level security;
alter table bills       enable row level security;
alter table messages    enable row level security;
alter table usage       enable row level security;
alter table tally_syncs enable row level security;
