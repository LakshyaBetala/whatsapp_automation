-- 046: a smart, non-spammy ASVA assistant.
--
-- Two small pieces of state:
--
-- platform_config: a tiny key/value store for founder-level switches. The first
--   key is 'assistant_enabled' - the global On/Off for the marketing/bot number's
--   AUTO-replies (owner commands like LIST always keep working; this only silences
--   the prospect auto-pitch). Lives here so it survives a backend restart/deploy.
--
-- leads: per-number funnel state for people who message the ASVA marketing/bot
--   number. So the bot pitches ONCE, then stays quiet; and when a lead says YES
--   (or the human takes over), it hands over and goes silent for a window
--   (handover_until) so it never talks over a real conversation. No message
--   content is stored - only status + timestamps.

CREATE TABLE IF NOT EXISTS platform_config (
    key text PRIMARY KEY,
    value text,
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
    from_number text PRIMARY KEY,
    status text DEFAULT 'new',        -- new | pitched | handover
    pitched_at timestamptz,
    handover_until timestamptz,
    msg_count integer DEFAULT 0,
    updated_at timestamptz DEFAULT now()
);
