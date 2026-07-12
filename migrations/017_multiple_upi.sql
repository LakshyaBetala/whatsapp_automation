-- 017_multiple_upi.sql
-- Add support for up to 3 UPI IDs in the business settings
ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS upi_vpa_2 text,
  ADD COLUMN IF NOT EXISTS upi_vpa_3 text;
