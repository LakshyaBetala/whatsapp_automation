-- 021: multi-company - one owner, several Tally companies.
--
-- Each Tally company = its own businesses row (own bills/clients/messages via
-- business_id scoping = per-company database). Sibling companies share the
-- owner's WhatsApp number, so the UNIQUE constraint on whatsapp_number must
-- relax to a plain index. The bot resolves an owner's number to the OLDEST
-- (primary) business deterministically.

alter table businesses drop constraint if exists businesses_whatsapp_number_key;
create index if not exists idx_businesses_whatsapp on businesses(whatsapp_number);
