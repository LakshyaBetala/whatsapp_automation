-- 049: Reconcile / archive parties that were DELETED in Tally.
--
-- A shop sometimes creates a party in Tally, then deletes it (a typo, a
-- duplicate, a wrong entry). ASVA kept the old row forever, so a ghost party
-- lingered in the list. The owner can now press "Clean up removed parties",
-- which (with a confirmation) archives any Tally-sourced client that is no
-- longer in Tally's debtor list.
--
-- Design is SOFT + SAFE:
--   * archived_at just marks the row; nothing is ever hard-deleted, so it is
--     fully reversible and no bills/history are lost.
--   * the same action also sets excluded = true (reuses the proven do-not-chase
--     filtering), so an archived party is GUARANTEED to get no reminders even if
--     a display filter is ever missed.
--   * a party that reappears in Tally is un-archived automatically.
--   * NON-Tally (manually added) parties are never touched.
--   * the archive step aborts if Tally sent a suspiciously short party list
--     (a partial/failed export), so a glitch can never wipe the list.

ALTER TABLE clients      ADD COLUMN IF NOT EXISTS archived_at          timestamptz;
ALTER TABLE businesses   ADD COLUMN IF NOT EXISTS reconcile_requested_at timestamptz;

-- Fast "show only live parties" filter for the dashboard.
CREATE INDEX IF NOT EXISTS idx_clients_active
  ON clients (business_id) WHERE archived_at IS NULL;
