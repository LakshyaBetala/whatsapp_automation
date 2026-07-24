-- 028_payment_promises.sql
-- Promise-to-Pay + reply capture (v1). When a customer replies to a reminder,
-- ASVA records a payment claim or a promised date here and HOLDS that party's
-- reminders until hold_until, then auto-resumes if still unpaid. This table is
-- also the promise ledger (who keeps their word) - the cross-shop asset.
--
-- Invariant: at most ONE row with status='open' per (business_id, client_id) at
-- a time. A newer reply supersedes the older open row (status='superseded').
-- Tenant isolation is by business_id in application code (service-role key,
-- no RLS), same as every other table.

CREATE TABLE IF NOT EXISTS payment_promises (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id      uuid NOT NULL,
    client_id        uuid NOT NULL,
    kind             text NOT NULL,                       -- 'paid_claim' | 'promise'
    amount           numeric,                             -- claimed/promised amount if extracted
    promise_date     date,                                -- future date for a promise; null for a bare claim
    hold_until       timestamptz NOT NULL,                -- reminders resume after this instant
    status           text NOT NULL DEFAULT 'open',        -- open | kept | broken | cancelled | superseded
    raw_text         text,                                -- the customer's exact words (audit + owner context)
    source           text,                                -- 'text' | 'screenshot' | 'keyword'
    confidence       numeric,                             -- classifier confidence 0..1 (null for keyword fast-path)
    followup_sent_at timestamptz,                         -- dedups the date-arrived follow-up pass
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- The sweep asks "which clients are on hold right now" per business; the
-- follow-up job scans open rows whose hold_until has passed.
CREATE INDEX IF NOT EXISTS idx_promises_biz_client_status
    ON payment_promises (business_id, client_id, status);
CREATE INDEX IF NOT EXISTS idx_promises_status_hold
    ON payment_promises (status, hold_until);
