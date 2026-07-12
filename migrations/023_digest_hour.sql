-- 023: per-business daily digest hour
-- The owner sets it from the bot ("DIGEST 9PM"). The digest job now runs
-- hourly and sends each business's digest once its own hour is reached
-- (with per-day dedup), so a laptop off at the hour catches up later.

alter table businesses add column if not exists digest_hour smallint;  -- 0-23, null = default (22)
