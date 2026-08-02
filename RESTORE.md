# ASVA - backup & restore runbook

ASVA writes real receipts into shops' books, so the database is the crown jewel.
This is how it is backed up and how to bring it back. **Read the "Restore drill"
once and actually run it on a throwaway project, so the first real restore isn't
your first restore.**

---

## 1. What holds the data

Everything lives in the **Supabase Postgres** project (the app is stateless; the
Windows host and the exe hold no primary data). So "backup" = the Postgres
database.

## 2. Turn on Point-in-Time Recovery (PITR) - do this now

PITR lets you restore to any second in the recent past (not just a nightly dump),
which is what you want after a bad sync or an accidental delete.

1. Supabase dashboard -> your project -> **Database -> Backups**.
2. Confirm **Point-in-Time Recovery** is **enabled**. (PITR is a paid add-on; on
   the free tier you get daily backups only - budget for PITR before you have
   more than one paying shop.)
3. Note the **retention window** shown (e.g. 7 days). That is how far back you can
   go; widen it as the pilot grows.

If PITR is not available on the current plan, at minimum confirm **daily backups**
are listed under Database -> Backups, and add the manual weekly dump below.

## 3. A second, off-Supabase copy (belt and suspenders)

Supabase backups live inside Supabase. Keep one copy somewhere else too, so a
project-level problem can't take the backup with it. Weekly is fine for the pilot.

From the host (or any machine with the DB URL), a plain SQL dump:

```powershell
# uses the same SUPABASE_DB_URL that is already in .env
$env:PGSSLMODE = "require"
pg_dump "$env:SUPABASE_DB_URL" -Fc -f "C:\ASVA\backups\asva_$(Get-Date -f yyyyMMdd).dump"
```

- `-Fc` is the compressed custom format (restore with `pg_restore`).
- Store the file off the host: OneDrive/Google Drive folder, or an external drive.
- Keep the **last 8 weekly dumps**; delete older ones.

(`pg_dump` ships with the PostgreSQL client tools. If it isn't on the host,
install "PostgreSQL command-line tools" once, or run the dump from any laptop
that has it.)

## 4. Restore drill (run once on a test project)

**A. Point-in-time (the usual case - undo a bad sync / accidental delete):**
1. Supabase dashboard -> Database -> Backups -> **Restore** (or **Point in Time**).
2. Pick the timestamp **just before** the bad event.
3. Confirm. Supabase provisions the restored state. Note the new connection
   details if they change.
4. Update `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_DB_URL` in the
   host's `.env` if the project ref changed, then restart ASVA (or just relaunch
   `ASVA_HOST.bat`).

**B. From the off-Supabase dump (worst case - project is gone):**
1. Create a fresh Supabase project.
2. Restore the schema + data:
   ```powershell
   pg_restore --clean --if-exists --no-owner -d "<NEW_DB_URL>" "C:\ASVA\backups\asva_YYYYMMDD.dump"
   ```
3. Re-apply any migrations newer than the dump from `migrations/` (in order).
4. Point the host `.env` at the new project and restart.

## 5. After ANY restore - verify before trusting it

Run these read checks (service-role) to confirm the data is sane:

```powershell
.\.venv\Scripts\python.exe -c "from app.db import get_client; c=get_client(); \
print({t: c.table(t).select('id',count='exact').limit(1).execute().count \
for t in ['businesses','clients','bills','tally_receipts','pending_receipts']})"
```

Sanity: `businesses` matches your shop count, `clients`/`bills` look right, and
open `pending_receipts` are ones you expect. Then open the Command Center
(`/ops`) and confirm shops show as online after their next heartbeat.

## 6. What is NOT in the database (and how it recovers on its own)

- **WhatsApp sessions** (`.baileys_auth`) live on each laptop, not in Postgres.
  A lost session just needs a one-time QR re-scan; no data is lost.
- **The installer + website** are rebuilt from the repo (`build_zip.py`,
  `build_installer.ps1`), not restored.
- **Tally is the source of truth for money.** Even in a bad case, a fresh
  `--import-masters` + `--sync` from each shop's Tally rebuilds bills and
  receipts. The database is a fast cache of Tally plus ASVA's own state
  (promises, pending receipts, messages) - so a restore is about ASVA's state,
  and Tally can always re-seed the accounting.

---

**Cadence:** PITR on (continuous) + one weekly off-Supabase dump. Re-run the
restore drill after any schema change large enough to worry you.
