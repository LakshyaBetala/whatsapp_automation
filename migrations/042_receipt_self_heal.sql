-- 042: self-healing receipt posting.
--
-- Problem: a receipt is CLAIMED by the agent (status flips confirmed -> posting),
-- the agent writes it to Tally, but the follow-up /receipts/report call is lost
-- (network blip, agent restart). The row then sits in 'posting' FOREVER - the
-- owner sees "Posting to Tally..." for hours and the payment never clears. The
-- old claim only re-handed 'confirmed' rows, never a wedged 'posting' one.
--
-- Fix: record WHEN a row entered 'posting' (posting_at). claim_confirmed now
-- reclaims any 'posting' row older than a short timeout (a healthy agent reports
-- within seconds), so a lost report self-heals on the next agent poll.
alter table pending_receipts add column if not exists posting_at timestamptz;

-- Any rows ALREADY wedged in 'posting' from before this migration have a null
-- posting_at, so the reclaim (posting_at is null OR older than the timeout)
-- picks them up on the very next poll - old stuck payments un-wedge themselves.
create index if not exists idx_pending_receipts_posting
  on pending_receipts (business_id, status, posting_at);
