#!/usr/bin/env bash
# ============================================================
#  ASVA backup setup for the i3 host. Run ONCE (safe to re-run).
#  Installs pg_dump (matching Supabase's Postgres) + rclone, registers the
#  nightly backup timer, runs a first LOCAL backup now, and prints the single
#  step to connect your Google Drive.
#
#      cd ~/asva  &&  bash deploy/linux/setup_backup.sh
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"
USER_NAME="$(whoami)"
[ -f .env ] || { echo "!! Run this from the ASVA server folder (.env not found)." >&2; exit 1; }
command -v apt-get >/dev/null 2>&1 || { echo "!! This installer is for Ubuntu/Debian (apt)." >&2; exit 1; }

echo "==> [1/4] pg_dump (Postgres client) ..."
# Supabase runs Postgres 15+, so pg_dump must be >= 15. Ubuntu's default can be
# older; if so, add the official PostgreSQL (PGDG) apt repo and install pg 16.
need_pg=1
if command -v pg_dump >/dev/null 2>&1; then
  v="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
  if [ "${v:-0}" -ge 15 ]; then need_pg=0; echo "    pg_dump $v OK"; fi
fi
if [ "$need_pg" -eq 1 ]; then
  echo "    installing postgresql-client-16 from PGDG ..."
  sudo apt-get install -y -qq curl ca-certificates gnupg lsb-release
  sudo install -d /usr/share/postgresql-common/pgdg
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | sudo gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq postgresql-client-16
fi

echo "==> [2/4] rclone (Google Drive uploader) ..."
command -v rclone >/dev/null 2>&1 || curl -fsSL https://rclone.org/install.sh | sudo bash
echo "    $(rclone version | head -1)"

echo "==> [3/4] nightly backup timer ..."
for u in asva-backup.service asva-backup.timer; do
  sed -e "s#__DIR__#$HERE#g" -e "s#__USER__#$USER_NAME#g" \
    "deploy/linux/$u" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now asva-backup.timer
echo "    next run: $(systemctl list-timers asva-backup.timer --no-pager 2>/dev/null | sed -n 2p | awk '{print $1, $2, $3}')"

echo "==> [4/4] first backup now (LOCAL - Drive starts after you connect it) ..."
bash deploy/linux/asva-backup.sh || true

cat <<EOF

============================================================
 LOCAL nightly backups are ON  ->  $HERE/backups/
 (kept 14 days locally; systemd runs them at 02:30 and catches up a missed night)

 To ALSO copy each backup to your Google Drive (2 TB), do this ONCE:

   rclone config
     n  (new remote)
     name>     asva-drive
     Storage>  drive              (type the word: drive)
     client_id / client_secret >  just press Enter (blank)
     scope>    1                  (full access)
     Edit advanced config>  n
     Use auto config>  n          (this box has no browser)
        -> it prints a command. Run THAT command on your laptop:
              rclone authorize "drive"
           a browser opens; log in; it prints a token; paste the token back here.
     Configure as team drive>  n
     y (keep)  then  q (quit)

   Test it:      rclone lsd asva-drive:
   From tonight, backups auto-upload to  asva-drive:ASVA_backups/

 Restore instructions:  deploy/linux/BACKUP.md
============================================================
EOF
