#!/usr/bin/env bash
# Bump the ASVA version in the two places that must agree:
#   app/config.py        app_version = "X.Y.Z"   (backend/server + heartbeat)
#   desktop/package.json "version": "X.Y.Z"      (electron-updater feed)
#
#   bash deploy/bump_version.sh 1.9.4
#
# It does NOT touch WHATS_NEW / releaseNotes - write those by hand (they are copy
# for the user, not a number). After bumping, build + push with push_release.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"
NEW="${1:-}"
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: bash deploy/bump_version.sh X.Y.Z" >&2; exit 1; }

cur=$(grep -oE 'app_version: str = "[0-9.]+"' app/config.py | grep -oE '[0-9.]+' | head -1 || true)
echo "==> app_version: ${cur:-?}  ->  $NEW"

# app/config.py
perl -0pi -e "s/app_version: str = \"[0-9.]+\"/app_version: str = \"$NEW\"/" app/config.py
# desktop/package.json  (only the top-level "version" line)
perl -0pi -e "s/(\"version\":\s*)\"[0-9.]+\"/\${1}\"$NEW\"/" desktop/package.json

echo "==> updated:"
grep -nE 'app_version: str = ' app/config.py
grep -nE '"version":' desktop/package.json | head -1
echo
echo "Next:"
echo "  1. (optional) edit WHATS_NEW in desktop/renderer/index.html + releaseNotes in desktop/package.json"
echo "  2. bash deploy/push_release.sh              # backend-only change (server.zip)"
echo "  2. bash deploy/push_release.sh --installer  # ALSO rebuild+ship the desktop installer feed"
