-- 030_business_share_data.sql
-- Moat toggle: opt-in (default ON) sharing of anonymised payment behaviour, so
-- ASVA can learn cross-shop patterns (typical payment period, who pays late) to
-- help every shop. No names, no bill details. This column only records the
-- owner's preference; the data product itself does not exist yet.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS share_data boolean NOT NULL DEFAULT true;
