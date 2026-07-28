-- 026_owner_language.sql
-- The OWNER's language for the ASVA app UI + the WhatsApp assistant's replies to
-- the owner. Chosen in the app, saved here, read by the bot so the owner is
-- answered in the same language as the app. Separate from msg_language, which is
-- the CUSTOMER-facing reminder language (per business/batch) and is unchanged.
alter table businesses add column if not exists owner_language text not null default 'english';
