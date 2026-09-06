-- 052_pay_link_events.sql
-- ============================================================================
-- "Can we tell that the customer paid, without waiting for them to say PAID?"
--
-- Not from a UPI deep link. upi://pay hands the customer to their own UPI app
-- and the money moves between THEIR bank and the shop's bank. Nothing calls
-- ASVA back. Confirmation only ever arrives two ways, and both already work:
--   * the customer replies PAID (or sends a screenshot), or
--   * the owner books the receipt in Tally and the next sync sees it.
-- ASVA deliberately never routes money, so there is no gateway webhook to hook.
--
-- What we CAN observe is INTENT, and it is worth a lot: the customer opened the
-- payment page, and then tapped "Pay by UPI" (their UPI app was handed the
-- amount). That is a strong, timely signal to nudge the owner to check their
-- bank - hours before the customer gets round to replying PAID.
--
-- Stored as EVENTS, never as "paid". The owner sees "opened your payment link
-- and tapped Pay", never "payment received" - claiming more than we know would
-- be worse than saying nothing.
-- ============================================================================

create table if not exists public.pay_link_events (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null,
  client_id    uuid,
  kind         text not null check (kind in ('opened', 'tapped')),
  amount       numeric,
  created_at   timestamptz not null default now()
);

comment on table public.pay_link_events is
  'Customer activity on the hosted pay page. INTENT ONLY - never treat as a confirmed payment.';

create index if not exists idx_pay_events_biz
  on public.pay_link_events (business_id, created_at desc);

-- Same lockdown as every other table: the app uses the service-role key
-- (BYPASSRLS), so deny-all here closes the public anon key with no code change.
alter table public.pay_link_events enable row level security;
