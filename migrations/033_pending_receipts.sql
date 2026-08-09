-- 033_pending_receipts.sql
-- The payment-entry queue. When the owner confirms a payment ("Ramesh paid 500
-- hdfc"), ASVA holds it here; the next time they open the app (with Tally open)
-- a popup shows it for confirm/edit, then the agent writes the Receipt into
-- Tally. Storing the posted voucher id lets a wrong entry be reverted.
create table if not exists pending_receipts (
  id               uuid primary key default gen_random_uuid(),
  business_id      uuid not null,
  client_id        uuid,
  party_ledger     text not null,          -- exact Tally ledger name to credit
  party_display    text,                   -- clean name for the popup
  amount           numeric(14,2) not null check (amount > 0),
  deposit_ledger   text not null default 'CASH',  -- CASH or a bank ledger name
  receipt_date     date not null default (now() at time zone 'Asia/Kolkata')::date,
  status           text not null default 'pending',  -- pending|posted|failed|skipped
  tally_voucher_id text,                   -- the created voucher's id (for revert)
  error            text,
  created_at       timestamptz not null default now(),
  posted_at        timestamptz
);
create index if not exists idx_pending_receipts_biz_status
  on pending_receipts (business_id, status, created_at);
