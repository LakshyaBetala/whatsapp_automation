-- 038: carry the payment-screenshot OCR hints onto the pending receipt so the
-- owner sees them at confirm time (who paid, the UPI reference, and how sure the
-- read was). These are SUGGESTIONS only - the owner still edits amount/date/
-- account and taps to post; ASVA never auto-posts to Tally.
alter table pending_receipts add column if not exists ocr_payer      text;
alter table pending_receipts add column if not exists ocr_ref        text;   -- UPI ref / UTR
alter table pending_receipts add column if not exists ocr_confidence numeric(3,2);  -- 0.00-1.00
