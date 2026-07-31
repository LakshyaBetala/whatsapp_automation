-- 029_receipt_pipeline.sql
-- Wire the payment-entry pipeline (PAID -> queue -> desktop confirm -> Tally).
--
-- 1. pending_receipts gains a 'confirmed' status in its lifecycle
--    (pending -> confirmed -> posted|failed|skipped). No DDL needed for that -
--    status is free text - but we widen the queue so the agent can pick up the
--    owner-approved rows and report back.
-- 2. The shop's Cash/Bank ledger names (the deposit accounts a receipt may debit)
--    are read from Tally by the agent and cached here so the confirm popup can
--    offer the owner their OWN accounts without a live Tally round-trip.

alter table businesses
  add column if not exists tally_deposit_ledgers      jsonb,
  add column if not exists tally_deposit_ledgers_at   timestamptz;

-- The exact FIFO allocation the agent applied, kept for audit / revert clarity.
alter table pending_receipts
  add column if not exists allocation jsonb,
  add column if not exists confirmed_at timestamptz;
