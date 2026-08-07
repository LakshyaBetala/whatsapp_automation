# ASVA database backups

Nightly `pg_dump` of the whole Supabase database, kept in **two** places so losing
the Supabase project (deleted, corrupted, or paused) is never the end:

- **Local** on the i3: `~/asva/backups/asva_db_YYYYMMDD_HHMMSS.sql.gz` (last 14 days)
- **Google Drive** (your 2 TB): `asva-drive:ASVA_backups/` (last 120 days)

A dump of a few thousand rows is only a few MB gzipped, so 2 TB holds many years.

## One-time setup (on the i3)

```bash
cd ~/asva
bash deploy/linux/setup_backup.sh
```

It installs `pg_dump` + `rclone`, turns on the nightly timer (02:30, catches up a
missed night), and runs the first LOCAL backup immediately. Then connect Google
Drive once with `rclone config` (the script prints the exact answers; on a headless
box you run `rclone authorize "drive"` on a laptop and paste the token back).

## Check it's working

```bash
systemctl list-timers asva-backup.timer     # when it next runs
cat ~/asva/backups/last_success             # last good backup + size
journalctl -u asva-backup.service -n 30     # last run's log
ls -lh ~/asva/backups/                       # local copies
rclone lsf asva-drive:ASVA_backups/          # copies on Google Drive
```

Run one by hand anytime: `bash deploy/linux/asva-backup.sh`

## Restore

1. Get a dump (from `~/asva/backups/` or `rclone copy asva-drive:ASVA_backups/<file> .`).
2. Decompress: `gunzip -k asva_db_*.sql.gz`
   (if encrypted: `gpg -d asva_db_*.sql.gz.gpg | gunzip > dump.sql`)
3. Restore. **Safest is a NEW/empty Supabase project**, then repoint `SUPABASE_URL` /
   `SUPABASE_SERVICE_KEY` / `SUPABASE_DB_URL` in `.env` and restart:

   ```bash
   psql "postgresql://postgres:PASSWORD@db.NEWREF.supabase.co:5432/postgres" < dump.sql
   ```

   Restoring over the LIVE database overwrites current data - only do that if you
   are deliberately rolling back.

## Options (optional, in `.env`)

| Key | Default | Meaning |
|-----|---------|---------|
| `BACKUP_RCLONE_REMOTE` | `asva-drive` | rclone remote name |
| `BACKUP_DRIVE_DIR` | `ASVA_backups` | folder in Google Drive |
| `BACKUP_KEEP_LOCAL_DAYS` | `14` | days of local dumps to keep |
| `BACKUP_KEEP_DRIVE_DAYS` | `120` | days of Drive dumps to keep |
| `BACKUP_GPG_PASSPHRASE` | (unset) | if set, dumps are encrypted before leaving the box (keep this passphrase safe - without it the backups can't be restored) |
