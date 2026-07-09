-- 013: message language for customer reminders.
-- 'hinglish' (default, Roman Hindi) or 'english'. The owner picks it in the
-- Reminders settings; the sweep swaps to the _en templates when 'english'.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS msg_language text DEFAULT 'hinglish';
