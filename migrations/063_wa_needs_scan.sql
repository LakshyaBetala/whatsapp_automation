-- Tell "nobody has linked a phone" apart from "the socket dropped and is
-- reconnecting". Both used to look the same (wa_ready = false), so a five
-- second reconnect could email the owner "re-scan the QR" - and after the
-- no-wipe fix in wa_service/index.js, re-scanning a healthy session is the
-- one action that actually destroys it.
--
-- Written by app/jobs/server_outbox.py from the shop process's own
-- `registered` flag: true = paired credentials exist on the server.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS wa_needs_scan boolean;

COMMENT ON COLUMN businesses.wa_needs_scan IS
  'Server-transport shops: true = no paired session, a human must scan the QR. '
  'false = credentials exist; a disconnect is just a reconnect in progress.';
