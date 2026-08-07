# Move the ASVA server off the home i3 onto a cloud VPS

Why: the i3 is a home PC. When home power or wifi drops, the WHOLE fleet stops
(the scheduler that queues reminders runs on it). A cloud VPS has 24/7 power, a
real connection, and a static IP. This reuses the deploy kit you already have.

What moves: the backend (API, scheduler, dashboard, updates feed) + the bot
WhatsApp (:3002, owner assistant). What does NOT move: each shop's own WhatsApp
and Tally agent stay on that shop's laptop (Tally is local) - they already
tolerate being off (outbox queue + 1.9.4 catch-up/self-heal).

> THE ONE RULE: never run the i3 backend AND the VPS backend at the same time.
> Two schedulers = every reminder sent twice. Stop the i3 services at cutover.

Budget: Hetzner CX22 (~EUR 4.5/mo, 2 vCPU / 4 GB) or AWS Lightsail ($5-10/mo).
Pick Ubuntu 24.04 LTS, 2 GB RAM or more.

---

## 1. Create the VPS
- Hetzner Cloud (or Lightsail) -> new server -> Ubuntu 24.04 -> 2 GB+ -> create.
- Note its public IP. Set a root/sudo password or add your SSH key.

## 2. First login + a normal user + Tailscale
SSH in as root (or the default user), then:
```bash
adduser asva && usermod -aG sudo asva      # a normal user to run ASVA
# Tailscale so push_release.sh reaches it exactly like the i3:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh                      # sign in (same account as the i3/laptop)
tailscale ip -4                              # note the 100.x.y.z  (this is your new I3_HOST)
```
Confirm from your laptop: `tailscale status` shows the VPS.

## 3. Put the code on the VPS
From your laptop (Git Bash), ship the current build over Tailscale:
```bash
cd /c/Users/laksh/whatsapp-tally-saas
.venv/Scripts/python build_zip.py server            # builds ~/Desktop/ASVA_server.zip
scp "$HOME/Desktop/ASVA_server.zip" asva@<VPS-TS-IP>:~/
ssh asva@<VPS-TS-IP> "mkdir -p ~/asva && unzip -o ~/ASVA_server.zip -d ~/asva"
```

## 4. Carry over the REAL config (Supabase keys, admin key, bot number)
The zip ships a fresh template .env. Overwrite it with the LIVE one from the i3
so the VPS uses the same database, admin key, and settings. Over Tailscale:
```bash
# from the i3 (or: scp i3 -> laptop -> VPS if you prefer)
scp ~/asva/.env  asva@<VPS-TS-IP>:~/asva/.env
```
Open `~/asva/.env` on the VPS and confirm SUPABASE_URL, SUPABASE_SERVICE_KEY,
ADMIN_API_KEY, and the bot settings are present. (Same DB = same shops, same
data, no re-onboarding.)

## 5. Install + start (one command - the kit does the rest)
On the VPS:
```bash
cd ~/asva && bash deploy/linux/setup_asva.sh
```
This installs Python, Node, the PDF libraries, and registers `asva-backend`
(:8000) + `asva-bot` (:3002) as systemd services that start on boot and restart
themselves. Wait for "ASVA is UP". Check: `curl -s localhost:8000/health`.

## 6. Give it the public address (Cloudflare Tunnel)
Easiest, no open ports, no TLS to manage:
1. Cloudflare -> Zero Trust -> Networks -> Tunnels -> Create tunnel -> name it
   `asva-vps` -> install the **Linux** connector command it shows, run it on the VPS.
2. Public Hostnames -> add two:
   - `app.tryasva.com`  -> HTTP -> `localhost:8000`
   - `link.tryasva.com` -> HTTP -> `localhost:3002`
3. Because a hostname can only point at one tunnel, adding it here moves it off
   the old i3 tunnel automatically.

## 7. Cutover (do these together, in order)
```bash
# a) STOP the i3 so two schedulers never run at once (do this FIRST):
ssh server-asva@100.101.127.38 "sudo systemctl disable --now asva-backend asva-bot"
#    (leave the i3's cloudflared off too, or delete its tunnel hostnames)

# b) confirm the VPS is serving publicly:
curl -s https://app.tryasva.com/health        # -> version 1.9.4, from the VPS

# c) re-scan the bot WhatsApp on the new host (one time):
#    open https://link.tryasva.com/qr and scan with 9344110272
```
Shops need NO change: they point at `app.tryasva.com`, which now resolves to the
VPS. Their reminders resume automatically.

## 8. Point future deploys at the VPS
Edit `deploy/remote.env` on your laptop:
```
I3_HOST=<VPS-TS-IP>
I3_USER=asva
I3_DIR=/home/asva/asva
```
Then `bash deploy/push_release.sh` deploys to the VPS exactly as before. Set up
the passwordless-restart sudoers drop-in on the VPS too (see REMOTE.md).

## 9. External monitor (so you always know)
UptimeRobot (free): monitor `https://app.tryasva.com/health` every 1 min, alert
to WhatsApp/SMS/email. This is the alarm the old setup never had.

---

## Rollback (if the VPS misbehaves)
```bash
# re-enable the i3, repoint the two Cloudflare hostnames back to the i3 tunnel:
ssh server-asva@100.101.127.38 "sudo systemctl enable --now asva-backend asva-bot"
# then STOP the VPS backend so only one scheduler runs:
ssh asva@<VPS-TS-IP> "sudo systemctl disable --now asva-backend asva-bot"
```
Keep the i3 as a warm standby for a week before repurposing it.
```
