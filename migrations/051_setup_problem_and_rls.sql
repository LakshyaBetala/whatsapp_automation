-- 051_setup_problem_and_rls.sql
-- ============================================================================
-- Two fixes that share one theme: a broken shop must never look healthy.
--
-- 1) SETUP PROBLEM (the MANJUSHA bug)
--    A shop whose Tally groups customers under a RENAMED reserved group (or a
--    group outside the Sundry Debtors tree) returned ZERO debtors on every
--    sync. The agent reported success, the backend logged records_synced=0 with
--    no error, and the owner's dashboard cheerfully said "All clear. Nothing
--    needs you right now." for days on a completely broken install.
--    The agent now reports WHY it found nothing; we store it here so both the
--    owner's dashboard and the Command Center can show a real problem instead
--    of a reassuring void.
--
-- 2) RLS on the two tables created after the 037 lockdown.
--    migrations/046 created public.platform_config and public.leads and never
--    enabled RLS, so both are readable/writable by anyone holding the public
--    ANON key (leads holds prospect phone numbers - personal data). This is
--    also what the Supabase security advisor flags.
--    The backend uses the SERVICE-ROLE key, which has BYPASSRLS, so enabling
--    RLS with no policy is deny-all for anon/authenticated and a NO-OP for the
--    app - exactly as 037 did for the other tables.
-- ============================================================================

-- 1) Setup problem reported by the agent (NULL = healthy).
alter table if exists public.businesses
  add column if not exists setup_problem     text,
  add column if not exists setup_problem_at  timestamptz;

comment on column public.businesses.setup_problem is
  'Last setup problem reported by the Tally agent (e.g. no debtor group found). NULL = healthy. Cleared automatically on the first sync that returns parties.';

-- 2) Close the two tables the 037 lockdown could not know about.
alter table if exists public.platform_config enable row level security;
alter table if exists public.leads           enable row level security;
