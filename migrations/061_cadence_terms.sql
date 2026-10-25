-- 061: the credit period actually reaches the cadence, and holidays are a choice.
--
-- Two columns, both owner-facing settings the engine now reads.
--
-- send_on_holidays: until now blackout_dates paused reminders unconditionally
-- and weekly_off_day was stored but never read at all, so a shop closed on
-- Sunday still chased on Sunday. The sweep now honours both and this is the
-- opt-out for a shop that trades through Sundays and festivals. Default false
-- = respect the off day, which is the safer behaviour for a customer
-- relationship and matches what the settings screen already implied.
--
-- default_credit_days already exists (migration 044). Nothing to add for it;
-- noted here because this is the migration that makes it matter: Tally reports
-- BILLCREDITPERIOD per ledger and almost never per bill, so the bill-wise
-- outstanding export arrives with no due date, and the backend used to fall
-- back to the invoice date - a silent 0-day term on every open bill of every
-- party. app/routers/tally.py now falls back to the party's own credit_days,
-- then this shop-wide default, then the trade-category norm.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS send_on_holidays boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN businesses.send_on_holidays IS
  'true = send reminders on the weekly off day and on blackout_dates too. '
  'false (default) = push them to the next working day.';
