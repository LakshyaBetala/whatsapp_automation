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
unzip -o "$ZIP" -x ".env" "downloads/*" "wa_service/.baileys_auth/*" -d "$HERE"
./.venv/bin/pip install -r requirements.txt --quiet
sudo systemctl restart asva-backend asva-bot

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
