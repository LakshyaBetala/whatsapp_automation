-- 064: a new party arrives with reminders OFF.
--
-- The fleet audit of 2026-11-01: every owner who actually opened ASVA had used
-- it to switch ASVA off. 915 of 1,043 parties at one shop, 270 of 272 at
-- another, 187 of 197 at a third. The shops with zero opt-outs were the ones
-- that never came back after the first sync.
--
-- The reminders were not the problem. "I will now chase all 1,043 of your
-- customers" was. So the owner picks who to chase (see app/services/shortlist)
-- and this default stops fighting him.
--
-- Existing rows are left exactly as they are: the 128 parties one owner chose
-- to keep on stay on, and a shop mid-collection does not go quiet overnight.
-- Only parties created from here on start off.

alter table if exists public.clients
  alter column reminders_enabled set default false;
