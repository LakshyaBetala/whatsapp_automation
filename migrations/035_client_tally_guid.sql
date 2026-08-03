-- 035_client_tally_guid.sql
-- Permanent fix for duplicate customers ("Thilak" vs "Thialk", same party):
-- match clients on Tally's stable per-ledger GUID, not the editable ledger name.
--
-- Until now a client was keyed only by tally_ledger_name (unique per business).
-- The moment a shop renamed a party in Tally, the next sync found no match on the
-- new name and CREATED a second client - splitting that party's outstanding
-- across two rows. Tally's GUID never changes on a rename or a number edit, so
-- storing it lets the backend update the same customer.
--
-- The column is nullable: existing rows have no GUID yet. The first GUID-aware
-- sync backfills each existing client's GUID by matching on its current ledger
-- name (see app/routers/tally.py), after which renames are handled forever.

ALTER TABLE public.clients
    ADD COLUMN IF NOT EXISTS tally_guid text;

-- One client per (business, GUID). Partial so the many NULL rows never collide.
CREATE UNIQUE INDEX IF NOT EXISTS clients_business_tally_guid_uq
    ON public.clients (business_id, tally_guid)
    WHERE tally_guid IS NOT NULL;
