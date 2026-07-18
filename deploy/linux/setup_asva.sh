#!/usr/bin/env bash
# ============================================================
#  ASVA host setup for Ubuntu / Debian (the i3). Run ONCE.
#  Installs EVERYTHING (Python, Node, the WeasyPrint PDF libraries), builds the
#  app, and registers two systemd services that start on boot and restart
#  themselves forever - no login, no lock screen. Safe to re-run.
#
#    unzip ASVA_server.zip -d ~/asva  &&  cd ~/asva
#    bash deploy/linux/setup_asva.sh
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root (script is in deploy/linux)
cd "$HERE"
USER_NAME="$(whoami)"
echo "==> ASVA host setup in: $HERE   (user: $USER_NAME)"

command -v apt-get >/dev/null 2>&1 || { echo "!! This installer is for Ubuntu/Debian (apt)." >&2; exit 1; }
[ -f .env ] || { echo "!! .env not found. Unzip the WHOLE ASVA_server.zip here first." >&2; exit 1; }
[ -f requirements.txt ] || { echo "!! requirements.txt missing - incomplete unzip." >&2; exit 1; }

echo "==> [1/6] System packages (Python + PDF libraries)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip curl unzip \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
  libffi-dev libgirepository-1.0-1 fonts-dejavu-core

echo "==> [2/6] Node.js..."
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
echo "    node $(node --version)"

echo "==> [3/6] Python environment..."
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt --quiet

echo "==> [4/6] WhatsApp service dependencies..."
( cd wa_service && npm install --no-fund --no-audit --silent )

echo "==> [5/6] Folders + systemd services..."
mkdir -p downloads
for svc in asva-backend asva-bot; do
  sed -e "s#__DIR__#$HERE#g" -e "s#__USER__#$USER_NAME#g" \
    "deploy/linux/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
# An always-on server must never suspend.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
sudo systemctl daemon-reload
sudo systemctl enable --now asva-backend asva-bot

echo "==> [6/6] Waiting for the backend to answer..."
ok=""
for _ in $(seq 1 25); do
  if curl -fsS http://localhost:8000/health >/tmp/asva_health 2>/dev/null; then ok=1; break; fi
  sleep 2
done

KEY="$(grep -E '^ADMIN_API_KEY=' .env | head -1 | cut -d= -f2- || true)"
echo
if [ -n "$ok" ]; then
  echo "==> ASVA is UP.  $(cat /tmp/asva_health)"
else
  echo "==> Backend not answering yet. Check:  journalctl -u asva-backend -n 60 --no-pager"
fi
echo
echo "   Command Center : http://localhost:8000/ops?key=$KEY"
echo "                    (public once the tunnel is up: https://app.tryasva.com/ops?key=$KEY)"
echo "   Bot QR (scan)  : http://localhost:3002/qr   (with the bot phone 9344110272)"
echo "   Publish app    : cp /path/to/ASVA_shop.zip $HERE/downloads/"
echo "   Logs           : journalctl -u asva-backend -f   |   journalctl -u asva-bot -f"
echo "   Status anytime : bash deploy/linux/asva_status.sh"
echo
echo "Next: the Cloudflare Tunnel + email alerts - see deploy/linux/README.md."
