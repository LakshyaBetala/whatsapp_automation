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

# Does this release actually change the WhatsApp service? Compare what is in
# the zip with what is installed, BEFORE unpacking over it. Anything we cannot
# determine counts as changed, so the safe answer is the default.
WA_CHANGED=$(./.venv/bin/python - "$ZIP" <<'PY' 2>/dev/null || echo 1
import hashlib, sys, zipfile, pathlib
zp, root = sys.argv[1], pathlib.Path(".")
try:
    with zipfile.ZipFile(zp) as z:
        names = [n for n in z.namelist()
                 if n.startswith("wa_service/") and not n.endswith("/")
                 and ".baileys_auth/" not in n and "node_modules/" not in n]
        if not names:
            print(0); raise SystemExit
        for n in names:
            live = root / n
            if not live.exists():
                print(1); raise SystemExit
            if hashlib.sha256(z.read(n)).digest() != hashlib.sha256(live.read_bytes()).digest():
                print(1); raise SystemExit
    print(0)
except SystemExit:
    raise
except Exception:
    print(1)
PY
)
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
# The backend always restarts: it IS the new code.
sudo -n systemctl restart asva-backend || {
  echo "!! Cannot restart services as $(whoami). Run as root:  systemctl restart asva-backend" >&2
  exit 1
}

# WhatsApp is restarted ONLY if its own code changed. An already-linked
# session survives a restart (about four seconds, no QR), but a pairing IN
# PROGRESS does not: Baileys writes creds.json only when the scan completes,
# so a restart in that window silently throws the pairing away and the owner
# scans a dead QR. Most deploys here are Python only, and those must not go
# anywhere near WhatsApp.
if [ "$WA_CHANGED" = "1" ]; then
  echo "==> wa_service changed - restarting WhatsApp services"
  sudo -n systemctl restart asva-bot || echo "!! could not restart asva-bot" >&2
  if systemctl is-enabled asva-shops >/dev/null 2>&1; then
    sudo -n systemctl restart asva-shops || echo "!! could not restart asva-shops (run as root)" >&2
  fi
else
  echo "==> wa_service unchanged - leaving WhatsApp connected (no restart, no QR)"
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
