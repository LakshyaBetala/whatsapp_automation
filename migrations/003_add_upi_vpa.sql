-- =====================================================================
-- 003 — Add UPI VPA to businesses for invoice payment links
--
-- Nullable text. If null, invoice shows "Contact for payment details"
-- instead of a UPI link. Owner sets this during onboarding or later.
-- =====================================================================

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upi_vpa text;
