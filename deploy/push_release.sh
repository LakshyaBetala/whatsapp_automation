#!/usr/bin/env bash
# ============================================================
#  Push a new ASVA build to the i3 host FROM THIS LAPTOP, over Tailscale.
#
#    bash deploy/push_release.sh                # build + ship server.zip, run update_asva.sh
#    bash deploy/push_release.sh --test         # run pytest first, abort if it fails
#    bash deploy/push_release.sh --installer     # ALSO build + ship the desktop auto-update feed
#    bash deploy/push_release.sh --test --installer
#
#  Needs deploy/remote.env (copy from remote.env.example) and Tailscale up on
#  both this laptop and the i3.  Nothing here touches the WhatsApp login, .env,
#  or the 4 shops' data - update_asva.sh preserves all of that.
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"

[ -f deploy/remote.env ] || { echo "!! deploy/remote.env missing. Copy deploy/remote.env.example -> deploy/remote.env and fill it in." >&2; exit 1; }
# shellcheck disable=SC1091
source deploy/remote.env
: "${I3_HOST:?set I3_HOST in deploy/remote.env}"; : "${I3_USER:?}"; : "${I3_DIR:?}"
PY=".venv/Scripts/python"; [ -f "$PY" ] || PY=".venv/bin/python"; [ -f "$PY" ] || PY="python"
SSH_OPTS=(-o ConnectTimeout=10)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
SSH()  { ssh "${SSH_OPTS[@]}" "$I3_USER@$I3_HOST" "$@"; }
SCP()  { scp "${SSH_OPTS[@]}" "$@"; }
TARGET="$I3_USER@$I3_HOST"

DO_TEST=0; DO_INSTALLER=0
for a in "$@"; do case "$a" in
  --test) DO_TEST=1;; --installer) DO_INSTALLER=1;;
  *) echo "unknown flag: $a" >&2; exit 1;; esac; done

echo "==> Target i3: $TARGET   dir: $I3_DIR"
echo "==> Reachability check (Tailscale/SSH)..."
SSH "echo ok" >/dev/null || { echo "!! Cannot SSH to $TARGET. Is Tailscale up on both ends? Try: tailscale status" >&2; exit 1; }
echo "    reachable."

if [ "$DO_TEST" = 1 ]; then
  echo "==> Running tests ("$PY" -m pytest -q)..."
  "$PY" -m pytest -q || { echo "!! Tests failed - not shipping." >&2; exit 1; }
fi

DESKTOP="$HOME/Desktop"
echo "==> Building ASVA_server.zip..."
"$PY" build_zip.py server
ZIP="$DESKTOP/ASVA_server.zip"
[ -f "$ZIP" ] || { echo "!! $ZIP not found after build." >&2; exit 1; }
echo "==> Shipping server.zip -> $TARGET:~/ASVA_server.zip"
SCP "$ZIP" "$TARGET:~/ASVA_server.zip"
echo "==> Applying on the i3 (update_asva.sh keeps .env, downloads, WhatsApp login)..."
SSH "cd '$I3_DIR' && bash deploy/linux/update_asva.sh ~/ASVA_server.zip"

if [ "$DO_INSTALLER" = 1 ]; then
  echo "==> Building the desktop installer (electron-builder)..."
  ( cd desktop && npm run dist )
  FEED="$I3_DIR/downloads/updates"
  echo "==> Shipping installer feed -> $TARGET:$FEED/"
  SSH "mkdir -p '$FEED'"
  # latest.yml + the versioned .exe + its .blockmap = the whole delta feed.
  SCP dist_installer/latest.yml dist_installer/ASVA-Setup-*.exe dist_installer/ASVA-Setup-*.exe.blockmap "$TARGET:$FEED/"
  echo "==> Installer feed published. Shops auto-update next time they open ASVA."
fi

echo
echo "==> DONE. Verifying health from here:"
SSH "curl -fsS http://localhost:8000/health" && echo || echo "(health check via SSH failed - check journalctl -u asva-backend -n 60)"
echo "   Command Center: https://app.tryasva.com/ops?key=<ADMIN_API_KEY>"
