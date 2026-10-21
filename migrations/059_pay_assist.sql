-- 059_pay_assist.sql
-- A customer who tapped "Pay" and then went quiet may have hit a problem: the
-- UPI app failed, the limit was hit, they lost the link. ASVA offers help once,
-- a few minutes later. This column is the once-only stamp, so a retry or a
-- second sweep can never send it twice.
alter table pay_link_events
  add column if not exists assist_sent_at timestamptz;

-- Owners who would rather ASVA stayed silent after a tap can switch it off.
alter table businesses
  add column if not exists pay_assist boolean not null default true;
