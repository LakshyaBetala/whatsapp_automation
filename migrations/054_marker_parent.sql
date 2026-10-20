-- 054_marker_parent.sql
-- A cadence marker records "this bill's reminder for cadence day N is taken".
-- It used to be written as 'sent' the moment the real message was QUEUED, so a
-- message that later expired (shop laptop off) left markers claiming a reminder
-- that never went out (894 markers, 380 customers on 20 Oct 2026).
-- Markers now point at the real message and follow its fate.
alter table public.messages
  add column if not exists parent_message_id uuid;

create index if not exists idx_messages_parent
  on public.messages (parent_message_id)
  where parent_message_id is not null;

-- One-off repair: drop markers whose real reminder (same customer, written in
-- the same burst, not itself a marker) expired or failed, so those customers
-- become due again. The daily caps spread the resends over days.
delete from public.messages m
 where m.template_name = 'cadence_marker'
   and m.parent_message_id is null
   and exists (
     select 1 from public.messages r
      where r.client_id = m.client_id
        and r.type = 'reminder'
        and coalesce(r.template_name, '') <> 'cadence_marker'
        and r.delivery_status in ('expired', 'failed')
        and abs(extract(epoch from (r.created_at - m.created_at))) < 5);
