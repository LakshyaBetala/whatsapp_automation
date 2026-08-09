-- 038_receipt_client_id.sql
-- ============================================================================
-- Data integrity (audit P1): link a Tally receipt to its client by ID, not just
-- by party_name text. Party names get renamed in Tally; an id does not. This
-- makes the reliability scorecard rename-proof and is the foundation for the
-- future consented buyer-owned score.
--
-- Purely additive + backfilled -> no app behaviour change. The backend now
-- writes client_id on every new receipt (app/routers/tally.py), and reads
-- (scorecard) prefer it, falling back to party_name for any unmatched history.
-- ============================================================================

alter table if exists public.tally_receipts
  add column if not exists client_id uuid references public.clients(id) on delete set null;

create index if not exists idx_tally_receipts_client
  on public.tally_receipts(business_id, client_id);

-- Backfill: match the stored party_name to a client's Tally ledger name
-- (preferred) or display name, within the same business.
update public.tally_receipts r
set client_id = c.id
from public.clients c
where r.client_id is null
  and c.business_id = r.business_id
  and r.party_name is not null
  and (c.tally_ledger_name = r.party_name or c.name = r.party_name);
