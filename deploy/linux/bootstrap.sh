#!/usr/bin/env bash
# ============================================================================
#  ASVA - FRESH UBUNTU BOX  ->  RUNNING, HARDENED SERVER.  One command.
#
#    scp -r <repo> root@NEW:/home/server-asva/asva      (code, no .env)
#    scp asva-state-*.tar.gz.enc root@NEW:/root/          (from export_state.sh)
#    ssh root@NEW  bash /home/server-asva/asva/deploy/linux/bootstrap.sh /root/asva-state-*.tar.gz.enc
#
#  Idempotent: safe to re-run. Every step this script performs is something
#  that was once done by hand on a box and then lost when the box was replaced.
#  Now the box is disposable and this script is the source of truth.
#
#  What it does, in order:
#    1  packages: Python 3.12, Node 20, PDF libs, pg_dump, fail2ban, ufw
#    2  the service user (server-asva, no sudo - the app never needs root)
#    3  python venv + pip, wa_service npm
#    4  import the state bundle (.env, bot secrets, tunnel, WhatsApp session)
#    5  cloudflared: install + run the SAME tunnel (public hostnames follow)
#    6  systemd units (backend, bot with a 0600 env file, nightly DB backup)
#    7  sshd hardening (key only) + fail2ban + ufw (rules FIRST, then enable)
#    8  tailscale (admin path) - joins with TS_AUTHKEY if set, else prints a URL
#    9  start everything and VERIFY: /health, bot status, tunnel
#
#  Ubuntu 22.04 / 24.04. Run as root.
# ============================================================================
set -euo pipefail

BUNDLE="${1:-}"
APP_USER="server-asva"
APP_DIR="/home/${APP_USER}/asva"
NODE_MAJOR=20
TS_HOST="${TS_HOST:-asva-vps}"

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m !! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m !! %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
[ -d "$APP_DIR/app" ] || die "code not found at $APP_DIR - scp the repo there first"
if [ -n "$BUNDLE" ]; then [ -f "$BUNDLE" ] || die "state bundle not found: $BUNDLE"; fi

# ── 1. packages ──────────────────────────────────────────────────────────
say "[1/9] system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg unzip git openssl \
  python3 python3-venv python3-pip \
  libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info \
  fail2ban ufw >/dev/null
# pg_dump must be >= the server. Supabase runs Postgres 17; Ubuntu 24.04 ships
# client 16, which REFUSES to dump a newer server ("server version mismatch").
# Take the client from the PostgreSQL apt repo instead.
if ! pg_dump --version 2>/dev/null | grep -qE ' 1[7-9]\.| [2-9][0-9]\.'; then
  install -d /usr/share/postgresql-common/pgdg
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release; echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list
  apt-get update -qq && apt-get install -y -qq postgresql-client-17 >/dev/null
fi
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -c2-3)" -lt "$NODE_MAJOR" ]; then
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
echo "   python $(python3 --version | cut -d' ' -f2)   node $(node -v)   pg_dump $(pg_dump --version | awk '{print $3}')"

# ── 2. service user ──────────────────────────────────────────────────────
say "[2/9] service user ${APP_USER} (no sudo, except restarting its own units)"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
chown -R "$APP_USER:$APP_USER" "/home/${APP_USER}"
# The ONLY root action the deploy user may take: restart the two ASVA units.
# push_release.sh runs update_asva.sh as this user and it must not need root
# for anything else. Exact commands, no wildcards, no shell.
cat > /etc/sudoers.d/asva-restart <<SUDO
${APP_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart asva-backend asva-bot, /usr/bin/systemctl restart asva-backend, /usr/bin/systemctl restart asva-bot
SUDO
chmod 440 /etc/sudoers.d/asva-restart
visudo -cf /etc/sudoers.d/asva-restart >/dev/null || die "sudoers rule invalid"

# ── 3. app dependencies ──────────────────────────────────────────────────
say "[3/9] python venv + wa_service"
sudo -u "$APP_USER" bash -c "
  cd '$APP_DIR'
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  cd wa_service && npm ci --omit=dev --silent 2>/dev/null || npm install --omit=dev --silent
"

# ── 4. state bundle ──────────────────────────────────────────────────────
if [ -n "$BUNDLE" ]; then
  say "[4/9] importing state from $(basename "$BUNDLE")"
  WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
  if [ -n "${ASVA_STATE_PASS:-}" ]; then
    openssl enc -d -aes-256-cbc -pbkdf2 -in "$BUNDLE" -pass env:ASVA_STATE_PASS | tar -C "$WORK" -xzf -
  else
    openssl enc -d -aes-256-cbc -pbkdf2 -in "$BUNDLE" | tar -C "$WORK" -xzf -
  fi
  S="$WORK/state"
  install -o "$APP_USER" -g "$APP_USER" -m 600 "$S/app/.env" "$APP_DIR/.env"
  [ -f "$S/etc/asva-bot.env" ] && install -o "$APP_USER" -g "$APP_USER" -m 600 "$S/etc/asva-bot.env" /etc/asva-bot.env
  if [ -d "$S/etc/cloudflared" ] && [ -f "$S/etc/cloudflared/config.yml" ]; then
    mkdir -p /etc/cloudflared && cp -a "$S/etc/cloudflared/." /etc/cloudflared/
    chmod 600 /etc/cloudflared/*.json /etc/cloudflared/token 2>/dev/null || true   # token-managed has no .json
  fi
  if [ -d "$S/app/baileys_auth" ]; then
    mkdir -p "$APP_DIR/wa_service/.baileys_auth"
    cp -a "$S/app/baileys_auth/." "$APP_DIR/wa_service/.baileys_auth/"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR/wa_service/.baileys_auth"
    chmod -R go-rwx "$APP_DIR/wa_service/.baileys_auth"
  fi
  [ -f "$S/etc/fail2ban/jail.local" ] && cp "$S/etc/fail2ban/jail.local" /etc/fail2ban/jail.local
  cat "$S/MANIFEST" | sed 's/^/   /'
else
  say "[4/9] no state bundle given - expecting .env and /etc/asva-bot.env to exist already"
  [ -f "$APP_DIR/.env" ] || die "no .env - pass the state bundle or create $APP_DIR/.env"
fi
# The bot service must have its own secret file even on a first-ever box.
# The bot env may already exist (a hand-made one carrying WEBHOOK_SECRET) yet
# lack the WA key. Check for the KEY, not the file: without it the bot serves
# /api/wa/send and /qr to anyone who can reach link.tryasva.com.
touch /etc/asva-bot.env
if ! grep -q '^WA_API_KEY=' /etc/asva-bot.env; then
  K="$(openssl rand -hex 24)"
  printf 'WA_API_KEY=%s\n' "$K" >> /etc/asva-bot.env
  if grep -q '^WA_API_KEY=' "$APP_DIR/.env"; then sed -i "s/^WA_API_KEY=.*/WA_API_KEY=$K/" "$APP_DIR/.env"; else printf '\nWA_API_KEY=%s\n' "$K" >> "$APP_DIR/.env"; fi
  warn "generated a new WA_API_KEY"
fi
grep -q '^WA_BIND_HOST=' /etc/asva-bot.env || echo 'WA_BIND_HOST=127.0.0.1' >> /etc/asva-bot.env
# The bot and the backend must share the webhook secret, or owner commands
# over WhatsApp are rejected as impersonation.
if ! grep -q '^WEBHOOK_SECRET=' /etc/asva-bot.env; then
  S="$(grep '^AISENSY_WEBHOOK_SECRET=' "$APP_DIR/.env" | cut -d= -f2-)"; [ -n "$S" ] || S="$(openssl rand -hex 24)"
  printf 'WEBHOOK_SECRET=%s\n' "$S" >> /etc/asva-bot.env
  grep -q '^AISENSY_WEBHOOK_SECRET=' "$APP_DIR/.env" || printf '\nAISENSY_WEBHOOK_SECRET=%s\n' "$S" >> "$APP_DIR/.env"
fi
chown "$APP_USER:$APP_USER" /etc/asva-bot.env; chmod 600 /etc/asva-bot.env

# ── 4b. swap ─────────────────────────────────────────────────────────────
# A 2 GB box runs ASVA fine, but a deploy (pip install) landing on top of the
# morning sync peak can touch 2 GB for a minute. Swap turns that into a slow
# minute instead of a killed service. Sized to half of RAM, 1 GB minimum.
if ! swapon --show | grep -q '^/swapfile'; then
  MB=$(( $(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo) / 2 )); [ "$MB" -lt 1024 ] && MB=1024; [ "$MB" -gt 4096 ] && MB=4096
  fallocate -l "${MB}M" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null; grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
  echo "   swap: ${MB} MB"
fi

# ── 5. cloudflared ───────────────────────────────────────────────────────
say "[5/9] cloudflared tunnel"
if ! command -v cloudflared >/dev/null 2>&1; then
  # arm64 too: the free always-on boxes (Oracle Ampere) are ARM.
  curl -fsSL -o /tmp/cf.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(dpkg --print-architecture).deb"
  dpkg -i /tmp/cf.deb >/dev/null
fi
# Two ways to carry the tunnel, both files:
#   * token-managed (current): /etc/cloudflared/token holds the dashboard token
#     and /etc/cloudflared/config.yml holds ONLY the ingress rules (the
#     dashboard's "published applications" were never pushed to cloudflared,
#     so the rules live here). CF_TUNNEL_TOKEN=... installs a fresh one.
#   * credential-file (older): config.yml names the tunnel + the .json credential.
# A carried bundle brings /etc/cloudflared/token; a fresh box gets the token
# on the command line. Either way the service is installed FROM the token.
if [ -z "${CF_TUNNEL_TOKEN:-}" ] && [ -f /etc/cloudflared/token ] && [ ! -f /etc/systemd/system/cloudflared.service ]; then
  CF_TUNNEL_TOKEN="$(tr -d '[:space:]' < /etc/cloudflared/token)"
fi
if [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
  cloudflared service uninstall >/dev/null 2>&1 || true
  cloudflared service install "$CF_TUNNEL_TOKEN" >/dev/null
fi
if [ ! -f /etc/cloudflared/config.yml ] && { [ -f /etc/cloudflared/token ] || [ -n "${CF_TUNNEL_TOKEN:-}" ]; }; then
  mkdir -p /etc/cloudflared
  cat > /etc/cloudflared/config.yml <<CF
ingress:
  - hostname: app.tryasva.com
    service: http://localhost:8000
  - hostname: link.tryasva.com
    service: http://localhost:3002
  - service: http_status:404
CF
fi
if [ -f /etc/cloudflared/config.yml ]; then
  if [ -f /etc/cloudflared/token ] && [ -f /etc/systemd/system/cloudflared.service ] \
     && ! grep -q -- '--config /etc/cloudflared/config.yml' /etc/systemd/system/cloudflared.service; then
    sed -i 's#--no-autoupdate tunnel run#--no-autoupdate --config /etc/cloudflared/config.yml tunnel run#' /etc/systemd/system/cloudflared.service
    systemctl daemon-reload
  fi
  [ -f /etc/systemd/system/cloudflared.service ] || cloudflared service install >/dev/null 2>&1 || true
  systemctl enable --now cloudflared >/dev/null 2>&1 || systemctl restart cloudflared
  echo "   tunnel will serve app. and link.tryasva.com from THIS box"
else
  warn "no tunnel on this box - pass CF_TUNNEL_TOKEN=<dashboard token> or carry /etc/cloudflared/* in the state bundle"
fi

# ── 6. systemd units ─────────────────────────────────────────────────────
say "[6/9] systemd services"
cat > /etc/systemd/system/asva-backend.service <<UNIT
[Unit]
Description=ASVA backend (FastAPI + scheduler, port 8000)
After=network-online.target
Wants=network-online.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
# EXACTLY ONE worker: the scheduler runs in-process and more workers would
# double every send. Concurrency comes from the threadpool (handlers are def).
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/asva-bot.service <<UNIT
[Unit]
Description=ASVA bot WhatsApp (owner assistant, YOUR number, port 3002)
After=network-online.target asva-backend.service
Wants=network-online.target

[Service]
# Secrets (WA_API_KEY, WA_BIND_HOST, WEBHOOK_SECRET) live in a 0600 file, never
# inline here where any local user could read them.
EnvironmentFile=/etc/asva-bot.env
User=${APP_USER}
WorkingDirectory=${APP_DIR}/wa_service
Environment=PORT=3002
Environment=WA_CHANNEL=bot
Environment=SESSION_ID=bot
Environment=BACKEND_URL=http://localhost:8000
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=6

[Install]
WantedBy=multi-user.target
UNIT

# Nightly database backup (pg_dump -> local, + Google Drive if rclone is set up).
cat > /etc/systemd/system/asva-backup.service <<UNIT
[Unit]
Description=ASVA nightly database backup

[Service]
Type=oneshot
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=/bin/bash ${APP_DIR}/deploy/linux/asva-backup.sh
UNIT
cat > /etc/systemd/system/asva-backup.timer <<UNIT
[Unit]
Description=Run the ASVA database backup every night at 02:30

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload

# ── 7. hardening ─────────────────────────────────────────────────────────
say "[7/9] sshd + fail2ban + ufw"
# Must sort FIRST: sshd uses the first value it reads, and Ubuntu's
# 50-cloud-init.conf sets PasswordAuthentication yes. A 99- file does nothing.
cat > /etc/ssh/sshd_config.d/00-asva-hardening.conf <<'CFG'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
CFG
sshd -t && systemctl reload ssh

[ -f /etc/fail2ban/jail.local ] || cat > /etc/fail2ban/jail.local <<'JAIL'
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd
JAIL
systemctl enable --now fail2ban >/dev/null

# Rules BEFORE enable so there is never a default-deny window without SSH.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow in on tailscale0 comment 'Tailscale admin path' >/dev/null
ufw allow 22/tcp comment 'SSH (key only, fail2ban)' >/dev/null
ufw allow 41641/udp comment 'Tailscale direct' >/dev/null
# 8000 and 3002 stay CLOSED to the internet: cloudflared reaches them on
# localhost, admin reaches them over Tailscale.
ufw --force enable >/dev/null
echo "   $(ufw status | head -1)"

# ── 8. tailscale ─────────────────────────────────────────────────────────
say "[8/9] tailscale"
command -v tailscale >/dev/null 2>&1 || { curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1; }
if tailscale status >/dev/null 2>&1; then
  echo "   already joined: $(tailscale ip -4)"
elif [ -n "${TS_AUTHKEY:-}" ]; then
  tailscale up --authkey "$TS_AUTHKEY" --hostname "$TS_HOST" --ssh=false
  echo "   joined as $TS_HOST: $(tailscale ip -4)"
else
  warn "run:  tailscale up --hostname $TS_HOST   (prints a login URL) - or pass TS_AUTHKEY=... to this script"
fi

# ── 9. start + verify ────────────────────────────────────────────────────
say "[9/9] starting services"
systemctl enable --now asva-backend asva-bot asva-backup.timer >/dev/null
sleep 10
H="$(curl -s --max-time 20 http://127.0.0.1:8000/health || true)"
echo "   backend  : ${H:-NOT ANSWERING}"
K="$(grep '^WA_API_KEY=' /etc/asva-bot.env | cut -d= -f2-)"
B="$(curl -s --max-time 12 -H "x-asva-key: $K" http://127.0.0.1:3002/api/wa/status || true)"
echo "   bot      : $(echo "$B" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("connected" if d.get("ready") else ("QR waiting - re-link from the Command Center" ))' 2>/dev/null || echo 'NOT ANSWERING')"
echo "   tunnel   : $(systemctl is-active cloudflared 2>/dev/null || echo 'not configured')"
echo "   backups  : $(systemctl is-active asva-backup.timer) (nightly 02:30)"
echo "   ssh      : $(sshd -T | grep -E '^passwordauthentication' | awk '{print "password auth", $2}')"
echo
case "$H" in *'"ok"'*) say "READY. Public check from your laptop:  curl https://app.tryasva.com/health";;
             *) die "backend did not come up - journalctl -u asva-backend -n 50";; esac
