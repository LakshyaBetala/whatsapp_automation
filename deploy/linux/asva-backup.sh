#!/usr/bin/env bash
# ============================================================
#  ASVA nightly database backup.
#  pg_dump the whole Supabase database -> gzip -> keep a LOCAL copy AND push a
#  copy to Google Drive (via rclone). Two locations = real durability, so losing
#  the Supabase project (deleted / corrupted / paused) is never the end.
#
#  Degrades gracefully: if the Google Drive remote isn't set up yet, it still
#  writes the LOCAL backup and tells you how to connect Drive. Run by the
#  asva-backup.timer nightly; safe to run by hand anytime:
#      bash deploy/linux/asva-backup.sh
#
#  Optional overrides in .env:
#    BACKUP_RCLONE_REMOTE   rclone remote name         (default: asva-drive)
#    BACKUP_DRIVE_DIR       folder in Google Drive     (default: ASVA_backups)
#    BACKUP_KEEP_LOCAL_DAYS keep local dumps for N days (default: 14)
#    BACKUP_KEEP_DRIVE_DAYS keep Drive dumps for N days (default: 120)
#    BACKUP_GPG_PASSPHRASE  if set, the dump is encrypted before it leaves the box
# ============================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"
LOG(){ echo "$(date '+%F %T') $*"; }

# Load .env (SUPABASE_DB_URL + any BACKUP_* overrides).
set -a; [ -f .env ] && . ./.env; set +a
DBURL="${SUPABASE_DB_URL:-}"
[ -n "$DBURL" ] || { LOG "!! SUPABASE_DB_URL not set in .env - cannot back up."; exit 1; }

REMOTE="${BACKUP_RCLONE_REMOTE:-asva-drive}"
DRIVE_DIR="${BACKUP_DRIVE_DIR:-ASVA_backups}"
KEEP_LOCAL_DAYS="${BACKUP_KEEP_LOCAL_DAYS:-14}"
KEEP_DRIVE_DAYS="${BACKUP_KEEP_DRIVE_DAYS:-120}"

BDIR="$HERE/backups"; mkdir -p "$BDIR"
STAMP="$(date '+%Y%m%d_%H%M%S')"

command -v pg_dump >/dev/null 2>&1 || { LOG "!! pg_dump not installed - run deploy/linux/setup_backup.sh"; exit 1; }

# --- Dump (schema + data). --no-owner/--no-privileges so it restores into any
#     Postgres/Supabase without role juggling. Encrypt if a passphrase is set. ---
LOG "Dumping database..."
if [ -n "${BACKUP_GPG_PASSPHRASE:-}" ]; then
  command -v gpg >/dev/null 2>&1 || { LOG "!! gpg not installed but BACKUP_GPG_PASSPHRASE is set"; exit 1; }
  FILE="$BDIR/asva_db_${STAMP}.sql.gz.gpg"
  pg_dump "$DBURL" --no-owner --no-privileges 2>>"$BDIR/backup.log" \
    | gzip -c \
    | gpg --batch --yes --passphrase "$BACKUP_GPG_PASSPHRASE" -c -o "$FILE"
  rc=${PIPESTATUS[0]}
else
  FILE="$BDIR/asva_db_${STAMP}.sql.gz"
  pg_dump "$DBURL" --no-owner --no-privileges 2>>"$BDIR/backup.log" | gzip -c > "$FILE"
  rc=${PIPESTATUS[0]}
fi
if [ "$rc" -ne 0 ]; then
  LOG "!! pg_dump failed (rc=$rc) - see $BDIR/backup.log"; rm -f "$FILE"; exit 1
fi

# Sanity: a real dump is never tiny. Guard against a silent empty/failed dump.
SIZE=$(stat -c%s "$FILE" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 500 ]; then
  LOG "!! backup file suspiciously small ($SIZE bytes) - refusing to keep it."; rm -f "$FILE"; exit 1
fi
LOG "Local backup OK: $FILE ($SIZE bytes)"

# --- Copy to Google Drive (rclone), if the remote is configured. ---
UPLOAD_FAIL=""
if command -v rclone >/dev/null 2>&1 && rclone listremotes 2>/dev/null | grep -q "^${REMOTE}:"; then
  if rclone copy "$FILE" "${REMOTE}:${DRIVE_DIR}/" --no-traverse 2>>"$BDIR/backup.log"; then
    LOG "Uploaded to ${REMOTE}:${DRIVE_DIR}/"
    rclone delete "${REMOTE}:${DRIVE_DIR}/" --min-age "${KEEP_DRIVE_DAYS}d" 2>>"$BDIR/backup.log" || true
  else
    LOG "!! Google Drive upload FAILED - the LOCAL backup is kept. Check rclone."; UPLOAD_FAIL=1
  fi
else
  LOG "!! Google Drive not connected yet (rclone remote '${REMOTE}' missing) - LOCAL backup only."
  LOG "   Connect it once with:  rclone config    (see deploy/linux/BACKUP.md)"
fi

# --- Prune old LOCAL dumps. ---
find "$BDIR" -maxdepth 1 -name 'asva_db_*.sql.gz*' -type f -mtime "+${KEEP_LOCAL_DAYS}" -delete 2>/dev/null || true

echo "$(date '+%F %T') OK ${FILE} (${SIZE} bytes)" > "$BDIR/last_success"
LOG "Done."
[ -n "$UPLOAD_FAIL" ] && exit 2 || exit 0
