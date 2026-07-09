-- 012: payment/account details the owner can edit (Accounts tab).
-- upi_vpa already exists (003). Add bank-transfer fields for when a
-- customer pays by NEFT/IMPS instead of UPI.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS bank_account_name text,
  ADD COLUMN IF NOT EXISTS bank_account_no   text,
  ADD COLUMN IF NOT EXISTS bank_ifsc         text,
  ADD COLUMN IF NOT EXISTS bank_name         text;
