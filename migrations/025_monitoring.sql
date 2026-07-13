-- 025_monitoring.sql : the operator health center.
--
-- Adds (a) live WhatsApp/queue status columns the shop reports on its heartbeat,
-- (b) a job heartbeat table so the monitor knows the scheduler jobs are running,
-- and (c) an alert log used both to show recent alerts and to DEDUP emails (one
-- mail per incident, not one every watchdog cycle).

-- (a) Shop-reported liveness (via POST /license/heartbeat).
alter table businesses add column if not exists wa_ready        boolean;
alter table businesses add column if not exists wa_checked_at   timestamptz;
alter table businesses add column if not exists outbox_pending  integer not null default 0;

-- (b) Scheduler job heartbeats: each job stamps its name on every run so the
-- monitor can say "reminder sweep ran 4 min ago" and alert if a job goes quiet.
create table if not exists job_heartbeats (
  job_name    text primary key,
  last_run_at timestamptz not null default now(),
  ok          boolean not null default true,
  detail      text,
  updated_at  timestamptz not null default now()
);

-- (c) Alerts: what the watchdog flagged. resolved_at IS NULL = still open; the
-- watchdog re-opens nothing already open (dedup) and resolves when healthy.
create table if not exists alert_log (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid references businesses(id) on delete cascade,
  kind        text not null,                 -- e.g. server_down, wa_down, outbox_stuck
  severity    text not null default 'warn',  -- info | warn | critical
  title       text not null,
  body        text,
  emailed     boolean not null default false,
  created_at  timestamptz not null default now(),
  resolved_at timestamptz
);

-- Fast "is there an open alert of this kind for this business?" lookup (dedup).
create index if not exists idx_alert_open
  on alert_log(kind, business_id, resolved_at);
create index if not exists idx_alert_recent
  on alert_log(created_at desc);
