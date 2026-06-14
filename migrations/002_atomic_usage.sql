-- =====================================================================
-- 002 — Atomic usage check for plan-limit enforcement
--
-- Problem: two concurrent WhatsApp sends at the plan limit can both
-- pass a SELECT→check→UPDATE sequence (TOCTOU race).
--
-- Solution: single function that INSERTs the month row if missing,
-- locks it with FOR UPDATE, checks the limit, and increments — all
-- inside one transaction.
--
-- Column names match 001_initial_schema.sql exactly:
--   usage.message_count  (NOT "messages_sent")
--   usage.period_month   (type: date)
--
-- p_period_month is optional (defaults to current month).  Passing it
-- explicitly lets tests exercise boundary behaviour without mocking
-- now().
-- =====================================================================

CREATE OR REPLACE FUNCTION increment_usage_if_allowed(
    p_business_id   uuid,
    p_limit         integer,
    p_period_month  date DEFAULT NULL
) RETURNS json
LANGUAGE plpgsql
AS $$
DECLARE
    v_count  integer;
    v_month  date;
BEGIN
    -- Resolve the billing month: caller-supplied or current.
    v_month := COALESCE(p_period_month, date_trunc('month', now())::date);

    -- Ensure a row exists for this (business, month) pair.
    -- ON CONFLICT is a no-op if the row already exists.
    INSERT INTO usage (business_id, period_month, message_count)
    VALUES (p_business_id, v_month, 0)
    ON CONFLICT (business_id, period_month) DO NOTHING;

    -- Lock the row so no concurrent caller can read-then-write.
    SELECT message_count INTO v_count
    FROM usage
    WHERE business_id = p_business_id
      AND period_month = v_month
    FOR UPDATE;

    -- Enforce the plan ceiling.
    IF v_count >= p_limit THEN
        RETURN json_build_object('allowed', false, 'count', v_count);
    END IF;

    -- Under the limit — increment and return the new count.
    UPDATE usage
    SET message_count = message_count + 1
    WHERE business_id = p_business_id
      AND period_month = v_month;

    RETURN json_build_object('allowed', true, 'count', v_count + 1);
END;
$$;
