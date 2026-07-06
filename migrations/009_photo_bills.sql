-- =====================================================================
-- 009 — Photo bills (OCR) + bill source tagging
--
-- Owner photographs a paper bill and WhatsApps it to the bot. OCR
-- extracts party/phone/amount; the owner confirms or corrects before
-- anything is sent. Confirmed photo bills become normal bills rows
-- (source='photo') and enter the reminder cadence like any Tally bill.
-- =====================================================================

ALTER TABLE bills
  ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'tally'
    CHECK (source IN ('tally', 'photo', 'manual'));

create table if not exists photo_bills (
  id            uuid primary key default gen_random_uuid(),
  business_id   uuid not null references businesses(id) on delete cascade,
  status        text not null default 'pending'
                  check (status in ('pending', 'confirmed', 'cancelled')),
  -- OCR-extracted fields (owner can correct before confirming)
  party_name    text,
  phone         text,
  amount        numeric(14,2),
  bill_number   text,
  bill_date     date,
  -- the original photo, forwarded to the customer on confirm
  image_b64     text,
  image_type    text default 'image/jpeg',
  bill_id       uuid references bills(id),   -- set on confirm
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists idx_photo_bills_pending
  on photo_bills(business_id, created_at desc) where status = 'pending';
