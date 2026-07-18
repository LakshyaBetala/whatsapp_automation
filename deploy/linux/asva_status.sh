#!/usr/bin/env bash
# One glance at the whole host: services, health, bot WhatsApp, tunnel.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
echo "== services =="
systemctl is-active asva-backend >/dev/null 2>&1 && echo "  asva-backend: UP" || echo "  asva-backend: DOWN"
systemctl is-active asva-bot     >/dev/null 2>&1 && echo "  asva-bot:     UP" || echo "  asva-bot:     DOWN"
systemctl is-active cloudflared  >/dev/null 2>&1 && echo "  cloudflared:  UP" || echo "  cloudflared:  (not a service / down)"
echo "== backend =="
curl -fsS http://localhost:8000/health 2>/dev/null && echo || echo "  not answering on :8000"
echo "== bot whatsapp =="
curl -fsS http://localhost:3002/api/wa/status 2>/dev/null && echo || echo "  not answering on :3002"
echo "== download published =="
ls -1 "$HERE/downloads" 2>/dev/null | sed 's/^/  /' || echo "  (downloads/ empty)"
