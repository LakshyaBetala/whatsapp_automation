#!/usr/bin/env bash
# ============================================================
#  ASVA host setup for Ubuntu (the i3). Run ONCE.
#  Installs Python + Node + the WeasyPrint PDF libraries, builds the venv,
#  installs the WhatsApp service, and registers two systemd services that
#  start on boot and restart themselves forever - no login, no lock screen.
#
#    unzip ASVA_server.zip -d ~/asva  &&  cd ~/asva
#    bash deploy/linux/setup_asva.sh
# ============================================================
set -euo pipefail

# repo root = two levels up from this script (deploy/linux/..)
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"
USER_NAME="$(whoami)"
echo "==> ASVA host setup in: $HERE  (user: $USER_NAME)"

if [ ! -f .env ]; then
  echo "!! .env not found. Unzip the WHOLE ASVA_server.zip here first." >&2
  exit 1
fi

echo "==> [1/5] System packages (Python, PDF libraries)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip curl \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
  libffi-dev libgirepository-1.0-1 fonts-dejavu-core

echo "==> [2/5] Node.js..."
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
node --version

echo "==> [3/5] Python environment..."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt --quiet

echo "==> [4/5] WhatsApp service dependencies..."
( cd wa_service && npm install --no-fund --no-audit )

echo "==> [5/5] systemd services (start on boot, auto-restart)..."
for svc in asva-backend asva-bot; do
  sed -e "s#__DIR__#$HERE#g" -e "s#__USER__#$USER_NAME#g" \
    "deploy/linux/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
# An always-on server must never suspend.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
sudo systemctl daemon-reload
sudo systemctl enable --now asva-backend asva-bot

echo
echo "==> Done."
echo "   Backend health : curl http://localhost:8000/health"
echo "   Bot QR (scan)  : http://localhost:3002/qr   (with the bot phone 9344110272)"
echo "   Logs           : journalctl -u asva-backend -f   |   journalctl -u asva-bot -f"
echo "   Restart        : sudo systemctl restart asva-backend asva-bot"
echo
echo "Next: install the Cloudflare Tunnel connector (see deploy/linux/README.md)."
