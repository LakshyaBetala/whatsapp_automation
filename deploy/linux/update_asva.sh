#!/usr/bin/env bash
# ============================================================
#  Update the ASVA host to a newer build (one command).
#  Overwrites the code, KEEPS your .env, downloads, and WhatsApp login,
#  reinstalls dependencies, and restarts the services.
#
#    bash deploy/linux/update_asva.sh /path/to/new/ASVA_server.zip
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"
ZIP="${1:-}"
{ [ -n "$ZIP" ] && [ -f "$ZIP" ]; } || { echo "usage: bash deploy/linux/update_asva.sh /path/to/ASVA_server.zip" >&2; exit 1; }

echo "==> Updating ASVA from $ZIP"
echo "    (keeping .env, downloads/, and the WhatsApp session)"
# Never overwrite the live config, the published downloads, or the WA auth.
if command -v unzip >/dev/null 2>&1; then
  unzip -oq "$ZIP" -x ".env" "downloads/*" "wa_service/.baileys_auth/*" -d "$HERE"
else
  # A box set up by hand may lack unzip; python is always there.
  python3 - "$ZIP" "$HERE" <<'PY'
import sys, zipfile
zf, dest = sys.argv[1], sys.argv[2]
skip = (".env", "downloads/", "wa_service/.baileys_auth/")
with zipfile.ZipFile(zf) as z:
    for m in z.infolist():
        if m.filename == ".env" or m.filename.startswith(skip[1:]):
            continue
        z.extract(m, dest)
PY
fi
./.venv/bin/pip install -r requirements.txt --quiet
# bootstrap.sh grants exactly this one command via /etc/sudoers.d/asva-restart.
sudo -n systemctl restart asva-backend asva-bot || {
  echo "!! Cannot restart services as $(whoami). Run as root:  systemctl restart asva-backend asva-bot" >&2
  exit 1
}
# Shop WhatsApp manager (only on a box that has it): picks up new wa_service
# code. Each shop process reconnects with its saved session, no QR.
if systemctl is-enabled asva-shops >/dev/null 2>&1; then
  sudo -n systemctl restart asva-shops || echo "!! could not restart asva-shops (run as root)" >&2
fi

# The backend takes ~8-12s to boot (imports + Supabase connect + scheduler), so
# POLL /health for up to 40s instead of giving one premature 4-second verdict
# (that produced a scary but false "not answering" every update).
echo -n "==> Waiting for the backend to come up"
ok=0
for _ in $(seq 1 20); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then ok=1; break; fi
  echo -n "."; sleep 2
done
echo
if [ "$ok" = "1" ]; then
  curl -fsS http://localhost:8000/health; echo
  echo "==> Updated and restarted."
else
  echo "!! Backend still not answering after 40s - check: journalctl -u asva-backend -n 60 --no-pager"
fi
