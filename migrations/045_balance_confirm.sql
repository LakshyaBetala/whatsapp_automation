-- 045: customer balance-confirm ("aap ke naam Rs X baaki hai, sahi hai?").
--
-- A low-frequency, OWNER-INITIATED message that asks a customer to confirm their
-- outstanding - turning the customer into a free reconciler and surfacing ledger
-- disputes early. Deliberately NOT automatic: the owner sends it per party, and
-- we dedup so the same customer is never asked more than once in 90 days.
--
-- last_balance_confirm_at: when this party was last asked to confirm.
-- message_type gains 'balance_confirm' so these sends are audited separately and
-- never counted as reminders (which would wrongly advance a bill's cadence).

ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_balance_confirm_at timestamptz;

-- ADD VALUE is idempotent with IF NOT EXISTS (Postgres 12+); safe to re-run.
ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'balance_confirm';
