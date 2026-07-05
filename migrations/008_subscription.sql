-- =====================================================================
-- 008 — Subscription lifecycle (the "license")
--
-- The product is the backend service, not the installed exe — so the
-- enforcement point is server-side and cannot be copied around:
--   trial/active -> (plan_expires_on passes) -> grace (5 days, warned)
--   -> suspended: ALL customer sends stop; only renewal notices flow.
-- Renewal = extend plan_expires_on (renew.py / dashboard later).
-- =====================================================================

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS subscription_status text NOT NULL DEFAULT 'trial'
    CHECK (subscription_status IN ('trial', 'active', 'grace', 'suspended')),
  ADD COLUMN IF NOT EXISTS plan_expires_on date;

-- Existing rows: 30 days from creation (or trial_ends_on if set)
UPDATE businesses
   SET plan_expires_on = COALESCE(trial_ends_on, created_at::date + 30)
 WHERE plan_expires_on IS NULL;
