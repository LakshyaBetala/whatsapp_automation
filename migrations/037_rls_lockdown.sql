-- 037_rls_lockdown.sql
-- ============================================================================
-- Defense-in-depth for tenant isolation (audit P0).
--
-- Every public table is exposed through PostgREST with the Supabase ANON key,
-- which is embeddable/public. Without RLS, anyone holding that anon key can read
-- or write these tables directly at https://<project>.supabase.co/rest/v1/... —
-- the real cross-tenant leak vector.
--
-- The ASVA backend uses the SERVICE-ROLE key (app/db.py -> create_client with
-- supabase_service_key), and service_role has the BYPASSRLS attribute. So
-- enabling RLS with NO permissive policy means:
--   * anon / authenticated  -> deny by default (the leak vector closes)
--   * service_role (the app) -> bypasses RLS -> ZERO behaviour change, no code edits
--
-- This is the exact end state we want: the app keeps working unchanged, and the
-- public API can no longer touch tenant data. Verified: no part of the codebase
-- uses the anon key (grep), so deny-all is safe.
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op if already enabled.
-- ============================================================================

-- 1) Tables the linter flagged as RLS-disabled -> enable RLS.
alter table if exists public.job_heartbeats   enable row level security;
alter table if exists public.wa_outbox         enable row level security;
alter table if exists public.app_releases      enable row level security;
alter table if exists public.alert_log         enable row level security;
alter table if exists public.pending_receipts  enable row level security;
alter table if exists public.pairing_codes     enable row level security;
alter table if exists public.support_requests  enable row level security;
alter table if exists public.tally_receipts    enable row level security;
alter table if exists public.payment_promises  enable row level security;
alter table if exists public.sweep_runs        enable row level security;
alter table if exists public.photo_bills       enable row level security;
alter table if exists public.inbound_messages  enable row level security;

-- Note: bills, businesses, clients, messages, tally_syncs, usage already have
-- RLS enabled with no policy -> already deny-all for anon, already correct. No
-- change needed for them (the linter lists them only as INFO, not an error).

-- 2) Pin search_path on the flagged functions so it is not role-mutable
--    (prevents search_path hijacking). "public, pg_temp" keeps them working
--    (they reference public objects) while making the path fixed. Loops over
--    every matching overload, so no argument signatures are needed here.
do $$
declare r record;
begin
  for r in
    select p.oid::regprocedure as sig
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in (
        'increment_usage_if_allowed', 'set_updated_at', 'plan_max_clients',
        'plan_max_messages', 'increment_usage')
  loop
    execute format('alter function %s set search_path = public, pg_temp', r.sig);
  end loop;
end $$;
