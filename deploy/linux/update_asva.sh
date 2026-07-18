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
sleep 4
if curl -fsS http://localhost:8000/health; then
  echo; echo "==> Updated and restarted."
else
  echo; echo "!! Backend not answering - check: journalctl -u asva-backend -n 60 --no-pager"
fi
