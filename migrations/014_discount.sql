-- =====================================================================
-- 014 — Early-payment discount percentage
--
-- Owners can offer a small "pay now" discount. When > 0, ASVA discounts
-- the amount shown to the customer AND the UPI QR amount by this percent,
-- and appends a discount line to reminders.
-- =====================================================================
ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS discount_pct numeric(5,2) NOT NULL DEFAULT 0;
