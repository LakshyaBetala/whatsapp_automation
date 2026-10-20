-- 056_wa_transport.sql
-- Where a shop's WhatsApp runs.
--   laptop  the shop's own computer delivers its queue (until 2.1.x, the only way)
--   server  ASVA's server runs the shop's WhatsApp (one process per shop, on its
--           own localhost port) and delivers the queue itself, so reminders and
--           bills go out while the laptop is off. The laptop then gets nothing to
--           send, so an old desktop can never double-send.
alter table public.businesses
  add column if not exists wa_transport text not null default 'laptop'
    check (wa_transport in ('laptop', 'server')),
  add column if not exists wa_port int unique,
  add column if not exists wa_server_requested_at timestamptz,
  add column if not exists wa_linked_at timestamptz;
