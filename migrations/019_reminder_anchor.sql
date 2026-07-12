-- 019: clients.reminder_anchor - "the day the owner selected this party".
--
-- Rule (simple enough for a 20-70 year old owner): the day you put a party
-- in a batch or switch its reminders ON, ASVA starts counting from THAT day.
-- For bills already overdue on that day, the overdue track runs from the
-- anchor (one polite reminder that day, then the overdue message every
-- ~7 days x3, then one owner escalation) instead of the old behaviour where
-- a long-overdue bill instantly fired its final owner-escalation and the
-- customer never received anything.
--
-- Bills not yet due are unaffected (the track uses max(due_date, anchor)).
-- When the column is null, the sweep falls back to the client's created_at.

alter table clients add column if not exists reminder_anchor date;
