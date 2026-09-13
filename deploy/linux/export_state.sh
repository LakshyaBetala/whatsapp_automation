#!/usr/bin/env bash
# ============================================================================
#  ASVA - export the server's PORTABLE STATE into one encrypted bundle.
#
#  Run on the OLD box as root:   bash deploy/linux/export_state.sh
#  Produces:                     /root/asva-state-YYYYMMDD-HHMM.tar.gz.enc
#
#  Everything else on a box is reproducible from the repo by bootstrap.sh.
#  These files are NOT, and together they are the box's identity:
#
#    .env                        Supabase keys, admin key, WA_API_KEY, base URL
#    /etc/asva-bot.env           bot service secrets (WA key, bind, webhook)
#    /etc/cloudflared/*          the TUNNEL. Carrying these means app. and
#                                link.tryasva.com follow the new box with ZERO
#                                Cloudflare dashboard work - the tunnel id is
#                                the public identity, not the IP.
#    wa_service/.baileys_auth/   the bot's WhatsApp login. Carrying it means
#                                NO re-scan on the new box.
#    /etc/fail2ban/jail.local    (nice to have)
#
#  The bundle is AES-256 encrypted with a passphrase you type; keep that
#  passphrase in your password manager, not in this repo.
# ============================================================================
set -euo pipefail

APP_DIR="${ASVA_DIR:-/home/server-asva/asva}"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="/root/asva-state-${STAMP}.tar.gz.enc"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$APP_DIR/.env" ] || { echo "no .env at $APP_DIR - wrong ASVA_DIR?" >&2; exit 1; }

echo "==> collecting state from $APP_DIR"
mkdir -p "$WORK/state/etc/cloudflared" "$WORK/state/app" "$WORK/state/etc/fail2ban"
cp "$APP_DIR/.env"                       "$WORK/state/app/.env"
[ -f /etc/asva-bot.env ]        && cp /etc/asva-bot.env        "$WORK/state/etc/asva-bot.env"
[ -d /etc/cloudflared ]         && cp -a /etc/cloudflared/.     "$WORK/state/etc/cloudflared/"
[ -f /etc/fail2ban/jail.local ] && cp /etc/fail2ban/jail.local "$WORK/state/etc/fail2ban/jail.local"
if [ -d "$APP_DIR/wa_service/.baileys_auth" ]; then
  # Only the LIVE session folders (session-*), never the .bak copies.
  mkdir -p "$WORK/state/app/baileys_auth"
  for d in "$APP_DIR"/wa_service/.baileys_auth/session-*; do
    [ -d "$d" ] && [[ "$d" != *.bak ]] && cp -a "$d" "$WORK/state/app/baileys_auth/"
  done
fi

# The bot's WEBHOOK_SECRET used to live inline in the systemd unit. If it is
# not already in /etc/asva-bot.env, lift it out so the new box gets it via the
# 0600 env file and never again via a world-readable unit.
if [ -f /etc/systemd/system/asva-bot.service ] && ! grep -q '^WEBHOOK_SECRET=' "$WORK/state/etc/asva-bot.env" 2>/dev/null; then
  WS="$(grep -oE '^Environment=WEBHOOK_SECRET=.*' /etc/systemd/system/asva-bot.service | cut -d= -f3- || true)"
  [ -n "$WS" ] && printf 'WEBHOOK_SECRET=%s\n' "$WS" >> "$WORK/state/etc/asva-bot.env"
fi

# What was captured - names only, never values.
{
  echo "exported_at=$(date -Is)"
  echo "from_host=$(hostname)"
  echo "tunnel_config=$([ -f /etc/cloudflared/config.yml ] && echo yes || echo no)"
  echo "baileys_sessions=$(ls -1d "$WORK"/state/app/baileys_auth/session-* 2>/dev/null | wc -l)"
} > "$WORK/state/MANIFEST"

# Passphrase from ASVA_STATE_PASS when set (scripted weekly pulls), else typed.
if [ -n "${ASVA_STATE_PASS:-}" ]; then
  echo "==> encrypting with ASVA_STATE_PASS"
  tar -C "$WORK" -czf - state | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:ASVA_STATE_PASS -out "$OUT"
else
  echo "==> encrypting (you will be asked for a passphrase twice)"
  tar -C "$WORK" -czf - state | openssl enc -aes-256-cbc -pbkdf2 -salt -out "$OUT"
fi
chmod 600 "$OUT"

echo
echo "  DONE  ->  $OUT   ($(du -h "$OUT" | cut -f1))"
echo
echo "  Copy it to your laptop now:"
echo "    scp root@$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):$OUT ."
echo "  Then on the NEW box:  bash deploy/linux/bootstrap.sh  $(basename "$OUT")"
