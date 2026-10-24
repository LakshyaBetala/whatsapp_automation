-- Tally freshness: know how old the figures are, and stop chasing on stale ones.
--
-- A shop does not leave TallyPrime open all day. When it is shut, ASVA keeps
-- sending reminders built from whatever it last read, so a customer who paid on
-- Tuesday gets chased on Thursday for money they already sent. That is worse
-- than sending nothing: it is exactly the relationship damage the product
-- exists to prevent, and the owner only finds out when the customer complains.
--
-- This got sharper the moment WhatsApp moved onto ASVA's server. Before, a shut
-- Tally usually meant a shut laptop, which meant nothing sent - the two
-- failures masked each other. The server sends regardless, so the staleness is
-- now visible to customers.
--
-- last_tally_sync_at: stamped by /tally/sync and /tally/import ONLY. Distinct
-- from last_seen, which the desktop heartbeat also stamps - an app that is open
-- with Tally closed keeps last_seen fresh while the figures rot.
--
-- tally_stale_ack_at: the owner answered "keep chasing on the old figures".
-- Lets the hold be overridden deliberately, for a bounded time, by the only
-- person entitled to make that call.

ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS last_tally_sync_at timestamptz,
    ADD COLUMN IF NOT EXISTS tally_stale_ack_at timestamptz;

-- Backfill so no existing shop is instantly judged stale on deploy: last_seen
-- is the closest thing we have to "ASVA heard from this shop", and the next
-- real sync replaces it within a minute of Tally opening.
UPDATE businesses
   SET last_tally_sync_at = last_seen
 WHERE last_tally_sync_at IS NULL;

COMMENT ON COLUMN businesses.last_tally_sync_at IS
    'Last time Tally data actually arrived (/tally/sync or /tally/import). NOT the heartbeat.';
COMMENT ON COLUMN businesses.tally_stale_ack_at IS
    'Owner said "keep chasing on the old figures"; overrides the staleness hold for a bounded window.';
