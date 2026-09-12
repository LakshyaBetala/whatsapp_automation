-- 053_pay_link_resolution.sql
-- ============================================================================
-- A tap on "Pay by UPI" is an EXPECTED payment: who, how much, for which bill,
-- when. This migration lets ASVA close the loop on it without ever guessing:
--
--   note         the UPI transaction note the customer's app was handed
--                ("INV 2526RTC0071 part") - which bill, whole or part.
--   resolved_at  when we learned what became of it.
--   resolved_by  how:
--                  tally      a Tally receipt for the party of about that amount
--                             arrived via sync (ground truth)
--                  screenshot the customer sent a payment screenshot
--                  owner      the owner pressed "Received, post to Tally"
--                  dismissed  the owner pressed "Not received"
--                  expired    nothing seen for 3 days
--                  superseded the customer tapped again with a new amount
--
-- Until resolved, the tap shows on the Payments tab as "Expected". A resolved
-- tap is history on the party page. Nothing here is ever shown as "paid".
-- ============================================================================

alter table public.pay_link_events
  add column if not exists note        text,
  add column if not exists resolved_at timestamptz,
  add column if not exists resolved_by text
    check (resolved_by is null or resolved_by in ('tally', 'screenshot', 'owner', 'dismissed', 'expired', 'superseded'));

create index if not exists idx_pay_events_open
  on public.pay_link_events (business_id, client_id, created_at desc)
  where kind = 'tapped' and resolved_at is null;
