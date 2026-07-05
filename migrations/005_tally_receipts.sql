-- =====================================================================
-- 005 — Receipt idempotency for full-FY Tally syncs
--
-- Tally ignores date filters over HTTP, so every /tally/sync sends the
-- whole financial year of vouchers. Sales dedup on (business_id,
-- tally_voucher_number) via the bills table; receipts need their own
-- ledger so a payment is never applied twice. Receipt voucher numbers
-- restart every year, hence receipt_date in the unique key.
-- =====================================================================

create table if not exists tally_receipts (
  id                   uuid primary key default gen_random_uuid(),
  business_id          uuid not null references businesses(id) on delete cascade,
  tally_voucher_number text not null,
  party_name           text,
  amount               numeric(14,2) not null default 0,
  receipt_date         date not null,
  created_at           timestamptz not null default now(),
  unique (business_id, tally_voucher_number, receipt_date)
);

create index if not exists idx_tally_receipts_business
  on tally_receipts(business_id);
