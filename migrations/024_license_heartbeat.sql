-- 024: license + heartbeat foundation (server-authoritative subscription)
--
-- The client (agent/desktop app) is a thin pipe. It calls POST /license/heartbeat
-- every ~30 min and OBEYS the server's answer: plan, expiry, remaining messages,
-- debtor cap, feature flags, and whether a newer version exists. The server is
-- the single source of truth - never the client (the "never trust the client"
-- rule). These columns support that:
--   license_key    - stable per-business licence identity (also shown to owner)
--   machine_id     - the machine the agent runs on (loose anti-copy signal)
--   last_seen      - last heartbeat time -> powers the ops health monitor
--   agent_version  - the client build that pinged -> update nudges

alter table businesses add column if not exists license_key   text;
alter table businesses add column if not exists machine_id    text;
alter table businesses add column if not exists last_seen      timestamptz;
alter table businesses add column if not exists agent_version  text;

-- Licence keys are unique when set (null allowed for rows not yet keyed).
create unique index if not exists idx_businesses_license_key
    on businesses (license_key) where license_key is not null;
