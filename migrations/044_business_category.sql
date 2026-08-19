-- 044: business trade category + a shop-wide default credit period.
--
-- Tally almost never carries a per-ledger BillCreditPeriod (in a live electricals
-- shop only 3 of 537 debtors had one). So when Tally has no credit period for a
-- party, ASVA must fall back to a sensible number - and "30 days" is wrong for a
-- trade like electricals where 60 days is normal. The owner picks their trade
-- once at setup; that sets default_credit_days. A real Tally BillCreditPeriod
-- always still wins per-party.
--
-- category: one of the known trades (see PLAN/CATEGORY_CREDIT_DAYS in
--           app/models.py) or 'other'. NULL is treated as 'other'.
-- default_credit_days: the effective fallback (from the category, owner-editable).

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS default_credit_days integer;
