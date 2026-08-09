-- 039_bill_settled_at.sql
-- Data integrity (audit P1, second half): a real settlement date per bill.
-- Until now a bill only had updated_at (changes on any edit), so "average days
-- to pay" could not be computed honestly. This adds settled_at, stamped with the
-- RECEIPT's date (the day the money actually came) when a bill is fully paid
-- during a Tally sync. Additive + nullable -> safe. Not backfilled on purpose:
-- we do not know the true historical pay date, so it accrues accurately from now
-- rather than polluting the metric with an approximation.
alter table if exists public.bills
  add column if not exists settled_at timestamptz;
