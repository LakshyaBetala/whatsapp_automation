-- 022: missed-hour catch-up confirmation
--
-- The hourly reminder sweep stamps every run into sweep_runs. If a batch's
-- send hour has NO stamp for today, ASVA was off at that hour - late sends
-- then WAIT for the owner's go-ahead instead of firing silently:
--   businesses.catchup_date + catchup_action ('send' | 'skip') record the
--   owner's decision for that day (dashboard banner buttons / owner alert).

create table if not exists sweep_runs (
  run_date date        not null,
  run_hour smallint    not null,
  ran_at   timestamptz not null default now(),
  primary key (run_date, run_hour)
);

alter table businesses add column if not exists catchup_date   date;
alter table businesses add column if not exists catchup_action text;  -- 'send' | 'skip'
