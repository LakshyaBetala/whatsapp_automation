# Move ASVA to a new server (monthly, ~20 minutes, no downtime you can see)

The box is disposable. Three things make that true:

1. **`deploy/linux/bootstrap.sh`** turns a fresh Ubuntu into a running, hardened
   ASVA server in one command. Every hand-done step ever performed on a box is
   in that script now, so nothing is lost when a box goes.
2. **The Cloudflare tunnel is portable.** `/etc/cloudflared/config.yml` and the
   tunnel's `.json` credential are files. Carry them and `app.tryasva.com` and
   `link.tryasva.com` follow the new box the moment `cloudflared` starts there.
   **Zero Cloudflare dashboard clicks.** The tunnel id is the public identity,
   not the IP.
3. **The bot's WhatsApp login is portable.** `wa_service/.baileys_auth/` moves
   with the box, so **no QR re-scan**.

Deploys target the Tailscale **name** `asva-vps`, not an IP, so
`push_release.sh` keeps working with no edit after a move.

---

## The four rules

- **Only ONE box runs the backend at a time.** Two schedulers = every reminder
  sent twice. Stop the old backend BEFORE starting the new one.
- **Only ONE box runs the bot at a time.** Two Baileys sessions on the same
  number = WhatsApp logs both out (code 440). Same rule.
- The **database is Supabase**, not on the box. Nothing to move. (The nightly
  `pg_dump` in `/home/server-asva/asva/backups` is a safety copy; take the latest
  with you anyway.)
- Never `git push`; the box is deployed by `scp`. It is not a git checkout.

---

## Step by step

### A. On the OLD box (2 minutes)

```bash
ssh root@asva-vps
bash /home/server-asva/asva/deploy/linux/export_state.sh     # asks for a passphrase
# -> /root/asva-state-YYYYMMDD-HHMM.tar.gz.enc
```

From your laptop:

```bash
scp root@asva-vps:/root/asva-state-*.tar.gz.enc .
scp "root@asva-vps:/home/server-asva/asva/backups/$(ssh root@asva-vps 'ls -t /home/server-asva/asva/backups/*.sql.gz | head -1 | xargs basename')" .
```

Put the passphrase in your password manager. The bundle holds every secret.

### B. Create the NEW box (5 minutes, provider dashboard)

- Ubuntu 24.04 LTS, 2 vCPU, 2 GB RAM (4 GB once past ~50 shops), 20 GB disk.
- Add your SSH public key at creation. Password login is disabled by bootstrap.
- Note the public IP. Nothing else on the provider side is needed, ever.

### C. Put the code and the bundle on it (3 minutes)

```bash
NEW=<public ip>
ssh root@$NEW 'useradd -m -s /bin/bash server-asva 2>/dev/null; mkdir -p /home/server-asva/asva'
# the repo, minus junk
rsync -az --delete --exclude .git --exclude .venv --exclude node_modules --exclude '*.log' \
      --exclude dist --exclude dist_agent --exclude build ./ root@$NEW:/home/server-asva/asva/
scp asva-state-*.tar.gz.enc root@$NEW:/root/
```

(No rsync on Windows? `scp -r` the same folders, or push a zip and unzip with
`python3 -m zipfile -e`.)

### D. Bootstrap (8 minutes, unattended)

```bash
ssh root@$NEW 'TS_AUTHKEY=tskey-auth-... bash /home/server-asva/asva/deploy/linux/bootstrap.sh /root/asva-state-*.tar.gz.enc'
```

It asks for the bundle passphrase (or set `ASVA_STATE_PASS=`). A Tailscale
**auth key** (Tailscale admin -> Settings -> Keys, reusable, tag it) lets it
join as `asva-vps` without a browser. Without one it prints a login URL.

**Do NOT start it yet on the public side.** bootstrap starts `cloudflared`, and
because the tunnel credential is the same, **the new box begins serving
`app.tryasva.com` immediately**. That is the cut-over. So:

### E. Cut over (the only 60 seconds that matter)

Run in this exact order, from your laptop:

```bash
# 1. OLD box: stop the scheduler + bot FIRST (kills the double-send risk)
ssh root@<old ip> 'systemctl disable --now asva-backend asva-bot cloudflared'

# 2. NEW box: bootstrap (step D). Its cloudflared takes the tunnel over.
#    (If you already ran D, just: ssh root@$NEW 'systemctl restart cloudflared asva-backend asva-bot')

# 3. Verify from the outside
curl https://app.tryasva.com/health           # {"status":"ok",...}
curl -o /dev/null -w '%{http_code}\n' https://link.tryasva.com/api/wa/status   # 401 = locked, good
```

Then open the Command Center: shops should show online within 5 minutes as
their agents heartbeat into the new box. Nothing on any shop laptop changes.

### F. Tailscale: retire the old name (1 minute)

Tailscale admin -> Machines -> the OLD `asva-vps` -> Remove. The new box owns
the name; `push_release.sh` now reaches it unchanged.

### G. Delete the old box after 24 quiet hours

Keep it stopped, not deleted, for a day. If anything looks wrong, rollback is:
`systemctl enable --now asva-backend asva-bot cloudflared` on the old box and
`systemctl disable --now ...` on the new one. Same tunnel, same session, so
rollback is as clean as the move.

---

## First box ever (no bundle to carry)

Run `bootstrap.sh` with no argument after creating `.env` by hand. It generates
`WA_API_KEY` itself. Create the tunnel once in the Cloudflare dashboard
(Zero Trust -> Networks -> Tunnels; public hostnames `app.` -> `localhost:8000`,
`link.` -> `localhost:3002`), put its `config.yml` + credential `.json` in
`/etc/cloudflared/`, re-run bootstrap, and re-link the bot from the Command
Center. From then on every move is A-G.

## What bootstrap hardens (so you never do it by hand again)

sshd key-only (`00-asva-hardening.conf`; must sort before cloud-init's `50-`),
fail2ban with the Tailscale range ignored, ufw default-deny with only 22 +
Tailscale open (8000/3002 reachable only via localhost and Tailscale), bot
secrets in a 0600 env file rather than the unit, `.baileys_auth` `go-rwx`, and
the nightly `pg_dump` timer at 02:30.
