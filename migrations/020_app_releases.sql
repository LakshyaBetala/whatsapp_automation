-- 020: app_releases - the Tally-style update channel.
--
-- YOU insert a row here (Supabase SQL editor or dashboard) when you ship a
-- new version. Every deployed ASVA (they all talk to this Supabase) compares
-- its own settings.app_version against the newest row and shows a friendly
-- "New version available" banner on the dashboard. Actual updating stays a
-- human step for now (download new zip, extract over the folder) - safe and
-- auditable at pilot scale; an auto-updater can come later.

create table if not exists app_releases (
  id           uuid primary key default gen_random_uuid(),
  version      text not null,           -- e.g. '1.2.0' (numeric dot parts)
  notes        text,                    -- one-line "what's new" shown to owners
  download_url text,                    -- where the new zip lives (optional)
  mandatory    boolean not null default false,
  created_at   timestamptz not null default now()
);

insert into app_releases (version, notes)
select '1.1.0', 'Navigation bar, reload button, update notices'
where not exists (select 1 from app_releases where version = '1.1.0');
