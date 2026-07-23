-- Morning pre-reminder checkpoint (Option A: hold + nudge Tally, never mark paid).
--
-- Before the sweep sends, ASVA previews today's reminder list to the owner on the
-- bot number. The owner replies PAID <n|name> to HOLD a party that already paid;
-- that reminder is skipped today and the owner is nudged to enter the receipt in
-- Tally. Nothing is written to Tally and no bill is marked paid here - Tally stays
-- the source of truth. A held party returns on its next cadence day until the
-- receipt appears in Tally.
--
-- State lives on the business row, exactly like catchup_* (migration 022):
--   checkpoint_date  - the IST date this preview was built for (dedup + "is it today")
--   checkpoint_items - ordered [{id,name,amount,days}] so "PAID 1" maps to a party
--   checkpoint_held  - client_ids the owner held today (the sweep skips these)
ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS checkpoint_date  date,
    ADD COLUMN IF NOT EXISTS checkpoint_items jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS checkpoint_held  jsonb NOT NULL DEFAULT '[]'::jsonb;
