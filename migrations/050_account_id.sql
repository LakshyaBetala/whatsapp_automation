-- 050: an explicit ACCOUNT link across a customer's Tally companies.
--
-- One company = one business = one subscription. A customer with two Tally
-- companies therefore has TWO businesses, and today "same owner" is only ever
-- INFERRED by matching whatsapp_number. That is fragile in both directions:
--   * the owner changes their number -> their companies are orphaned from
--     each other;
--   * two unrelated shops that happen to share a number -> silently merged.
-- account_id makes the relationship explicit. The PRIMARY (oldest) business of
-- an account is its own account_id, so a single-company shop is simply its own
-- account and nothing about existing behaviour changes.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS account_id uuid;

-- Backfill: every existing business joins the account of the OLDEST business
-- sharing its whatsapp_number (which is exactly the "oldest = primary" rule the
-- bot already used implicitly).
WITH primary_of AS (
  SELECT DISTINCT ON (whatsapp_number) whatsapp_number, id AS primary_id
  FROM businesses
  WHERE whatsapp_number IS NOT NULL AND btrim(whatsapp_number) <> ''
  ORDER BY whatsapp_number, created_at
)
UPDATE businesses b
SET account_id = p.primary_id
FROM primary_of p
WHERE b.whatsapp_number = p.whatsapp_number
  AND b.account_id IS DISTINCT FROM p.primary_id;

-- A business with no number is its own account.
UPDATE businesses SET account_id = id WHERE account_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_businesses_account ON businesses (account_id);
