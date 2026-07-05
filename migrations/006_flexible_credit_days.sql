-- =====================================================================
-- 006 — Flexible credit periods
--
-- Real Tally data has per-customer terms like '1 Days', '45 Days',
-- '75 Days' — the original check constraint (30/60/90/120/180 only)
-- rejects them. Allow anything from 1 to 365 days.
-- =====================================================================

ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_credit_days_check;
ALTER TABLE clients ADD CONSTRAINT clients_credit_days_check
  CHECK (credit_days >= 1 AND credit_days <= 365);
