-- 048: smart onboarding nudges (dedup stamps).
--
-- welcomed_at        : set once, the first time a shop's Tally data syncs, so the
--                      "you're all set" welcome to the owner is sent exactly once.
-- unsynced_nudge_at  : set once when we nudge a paired-but-never-synced shop to
--                      finish setup (open ASVA + Refresh) before its code lapses.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS welcomed_at       timestamptz;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS unsynced_nudge_at timestamptz;
