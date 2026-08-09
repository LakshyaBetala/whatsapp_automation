-- 035_support_requests.sql
-- When an owner sends "TEAM <message>" (or SUPPORT/PROBLEM/MADAD), ASVA forwards
-- it to the product team's WhatsApp AND records it here, so the operator can see
-- every request in the Command Center: who asked, when, and whether it's handled.
create table if not exists support_requests (
  id            uuid primary key default gen_random_uuid(),
  business_id   uuid,
  business_name text,
  from_number   text,
  message       text not null,
  status        text not null default 'open',   -- open | resolved
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz
);
create index if not exists idx_support_requests_status
  on support_requests (status, created_at desc);
