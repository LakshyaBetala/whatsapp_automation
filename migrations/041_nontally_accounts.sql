-- 041_nontally_accounts.sql
-- Make the non-Tally party page a full accounts handler:
--   1. Fix party/bill deletion. photo_bills.bill_id referenced bills(id) with NO
--      on-delete rule (defaults to RESTRICT), so deleting a bill that was ever a
--      photo bill was blocked - and that failed the whole "Delete party". Switch
--      it to ON DELETE SET NULL so a bill can always be deleted (the photo_bills
--      history row survives with a null bill_id).
--   2. Add a payment LOG for non-Tally parties (manual_payments): each recorded
--      payment is kept with its date, so the party page can show a real history
--      (Tally parties keep using tally_receipts).

-- ── 1. photo_bills.bill_id -> ON DELETE SET NULL ─────────────────────────────
DO $$
DECLARE
  con text;
BEGIN
  SELECT conname INTO con
  FROM pg_constraint
  WHERE conrelid = 'public.photo_bills'::regclass
    AND contype = 'f'
    AND 'bill_id' = ANY (
      SELECT attname FROM pg_attribute
      WHERE attrelid = 'public.photo_bills'::regclass AND attnum = ANY (conkey));
  IF con IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.photo_bills DROP CONSTRAINT %I', con);
  END IF;
  ALTER TABLE public.photo_bills
    ADD CONSTRAINT photo_bills_bill_id_fkey
    FOREIGN KEY (bill_id) REFERENCES public.bills(id) ON DELETE SET NULL;
END $$;

-- ── 2. manual_payments: non-Tally payment history ───────────────────────────
CREATE TABLE IF NOT EXISTS public.manual_payments (
  id            uuid primary key default gen_random_uuid(),
  business_id   uuid not null references public.businesses(id) on delete cascade,
  client_id     uuid references public.clients(id) on delete cascade,
  amount        numeric(14,2) not null,
  payment_date  date not null default current_date,
  note          text,
  created_at    timestamptz not null default now()
);
CREATE INDEX IF NOT EXISTS idx_manual_payments_client
  ON public.manual_payments (business_id, client_id, payment_date DESC);

-- A new table defaults to RLS OFF, which would expose it to the anon/PostgREST
-- role. The whole DB is locked down (migration 037): the backend uses the
-- service-role key and bypasses RLS, so enable RLS with NO policy = deny anon.
ALTER TABLE public.manual_payments ENABLE ROW LEVEL SECURITY;
