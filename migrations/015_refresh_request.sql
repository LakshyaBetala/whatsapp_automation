-- 015_refresh_request.sql
-- "Reload data" override switch. The dashboard sets refresh_requested_at when
-- the owner presses Reload; the Tally agent polls it and, when set, runs an
-- immediate outstanding refresh (instead of waiting for its 5-min auto cycle),
-- then clears it. Rate-limited to once / 10 min in the backend.
alter table businesses
  add column if not exists refresh_requested_at timestamptz;
