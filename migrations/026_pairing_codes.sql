-- 026: pairing codes - passwordless onboarding for the thin client.
--
-- The operator (Command Center, ADMIN_API_KEY gated) mints a short, single-use
-- code bound to a business. The shop's installer asks the owner for that code
-- and calls POST /license/pair {code}; the server returns that business's
-- agent_token + id, which the app stores locally. So NO token is ever typed or
-- pasted by hand, and NOTHING secret ships inside the public installer - the
-- code is the only bearer credential, and it is single-use and short-lived.
--
-- A code can target a NEW business or an EXISTING one. Re-pairing onto an
-- existing business_id is how the pilot owner moves off the old standalone
-- WITHOUT redoing anything: every reminder/schedule lives in the DB under that
-- same business_id, so a fresh install that pairs to it inherits them all.

create table if not exists pairing_codes (
  code         text primary key,                 -- canonical, no separators, e.g. K7P29M4T
  business_id  uuid not null references businesses(id) on delete cascade,
  note         text,                             -- 'new-business' | 're-pair' | free text
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  used_at      timestamptz                       -- set once, on redeem (single use)
);

create index if not exists idx_pairing_codes_business on pairing_codes(business_id);
create index if not exists idx_pairing_codes_open
  on pairing_codes(expires_at) where used_at is null;
