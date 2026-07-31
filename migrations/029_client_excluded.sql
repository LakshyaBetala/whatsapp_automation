-- 029_client_excluded.sql
-- "Do not chase" list. Some parties will never pay; the owner marks them
-- excluded so they stop appearing in reminders and in the morning "who to chase"
-- checkpoint. Different from reminders_enabled (a customer opt-out) - this is the
-- owner writing a party off. Reversible (owner sends INCLUDE <name>).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS excluded boolean NOT NULL DEFAULT false;
