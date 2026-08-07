# ASVA - Remote access + deploy from afar (the 4-days-away kit)

Two things this gives you while you are out of station:
1. **Reach the i3 at home** from your laptop (Tailscale - a private network, no
   router setup, no public IP).
2. **Push a new server build to the i3 from your laptop** in one command.

The payment fixes (1.9.4) are **backend-only** - they ride in `ASVA_server.zip`.
**No new .exe / blockmap is needed**; the desktop app does not change. So a
remote update is just: apply one DB migration, then push server.zip.

---

## A. Set up remote access - DO THIS ON THE i3 NOW (before you leave)

On the **i3** (it is Ubuntu):
```bash
# 1. Tailscale (mesh VPN - makes the i3 reachable from anywhere, securely)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # opens a login URL - sign in with your Google account
tailscale ip -4              # note the 100.x.y.z address it prints
sudo tailscale set --ssh     # allow Tailscale SSH (no password, key-based, private)

# 2. Make sure OpenSSH is installed (fallback if you prefer plain ssh)
sudo apt-get install -y openssh-server && sudo systemctl enable --now ssh

# 3. Confirm the ASVA services are enabled to survive reboots
systemctl is-enabled asva-backend asva-bot
```

On **this laptop** (do it now too, same Tailscale account):
```powershell
winget install Tailscale.Tailscale     # or download from tailscale.com/download
tailscale up                            # sign in with the SAME Google account
tailscale status                        # you should see the i3 listed by name
```

Now `ssh <i3-user>@<i3-tailscale-name>` works from anywhere. Test it once before
you leave.

### One-time: let remote deploys restart the services without a terminal
`update_asva.sh` ends with `sudo systemctl restart asva-backend asva-bot`, and a
non-interactive SSH session can't type a sudo password. Grant passwordless sudo
for JUST those restarts (run once on the i3):
```bash
echo 'server-asva ALL=(root) NOPASSWD: /usr/bin/systemctl restart asva-backend asva-bot, /usr/bin/systemctl restart asva-backend, /usr/bin/systemctl restart asva-bot' | sudo tee /etc/sudoers.d/asva-deploy
sudo chmod 440 /etc/sudoers.d/asva-deploy
```
(Use the actual i3 username instead of `server-asva` if different. Verify with
`sudo -n systemctl is-active asva-backend` from your laptop over SSH - it should
print `active`, not ask for a password.)

---

## B. Configure the deploy tool (one time, on this laptop)

```bash
cp deploy/remote.env.example deploy/remote.env
# edit deploy/remote.env:
#   I3_HOST = the i3's Tailscale name (from `tailscale status`) or its 100.x IP
#   I3_USER = your Ubuntu username on the i3
#   I3_DIR  = the ASVA repo path on the i3 (holds .venv + .env), e.g. /home/you/asva
```
`deploy/remote.env` is git-ignored (it names your private host).

---

## C. Deploy 1.9.4 (or any future build) remotely

**Step 1 - apply the DB migration to PRODUCTION Supabase (once per new migration).**
The new payment code needs the `posting_at` column. Do this BEFORE pushing code.
Easiest: Supabase dashboard -> SQL Editor -> paste the contents of
`migrations/037_receipt_self_heal.sql` -> Run. (It is `add column if not exists`,
so it is safe to run once, and safe to re-run.)

> Do this **now, before you leave** if you can - then a code push during the 4
> days needs no DB access at all.

**Step 2 - push the server build from this laptop:**
```bash
bash deploy/push_release.sh            # builds server.zip, ships it, runs update_asva.sh on the i3
# or, to run the tests first:
bash deploy/push_release.sh --test
```
That's it. `update_asva.sh` keeps the i3's `.env`, downloads, and WhatsApp login,
reinstalls deps, restarts `asva-backend` + `asva-bot`, and health-checks. The 4
shops auto-pull nothing for this update (no desktop change) - they just talk to
the updated backend.

**If a FUTURE change also touches the desktop app** (main.js / renderer), then and
only then:
```bash
bash deploy/bump_version.sh 1.9.5      # bump both version spots
bash deploy/push_release.sh --installer # ALSO builds + ships the exe/blockmap/latest.yml feed
```

---

## D. If something looks wrong from afar

```bash
ssh <i3> "journalctl -u asva-backend -n 80 --no-pager"   # backend logs
ssh <i3> "journalctl -u asva-bot -n 80 --no-pager"       # bot WhatsApp logs
ssh <i3> "bash <path>/deploy/linux/asva_status.sh"       # one-glance status
```
Command Center (from any browser): `https://app.tryasva.com/ops?key=<ADMIN_API_KEY>`
Re-scan the bot WhatsApp: `https://link.tryasva.com/qr` (scan with 9344110272).
