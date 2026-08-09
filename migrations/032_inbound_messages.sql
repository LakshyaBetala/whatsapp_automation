-- 032_inbound_messages.sql
-- Remember what customers say. Until now we stored only a promise's raw_text and
-- an inbound message id (for dedup) - so the owner could never see a party's
-- story ("reminded 3 times, replied once, went quiet") and the payment-behaviour
-- dataset (our long-term moat) was thrown away. This stores every inbound reply,
-- linked to the party, so the tracker and the dataset both have their source.
create table if not exists inbound_messages (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  client_id   uuid,                       -- null when the sender is not a known party yet
  from_number text,
  body        text not null,
  intent      text,                       -- paid_claim | promise | dispute | chatter | unclear | keyword | screenshot
  created_at  timestamptz not null default now()
);
create index if not exists idx_inbound_biz_client_time
  on inbound_messages (business_id, client_id, created_at desc);
