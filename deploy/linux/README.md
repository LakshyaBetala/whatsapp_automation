# ASVA host on Ubuntu (the i3)

The Linux replacement for `ASVA_HOST.bat`. Ubuntu is the better home for the
host: no lock screen, no display-wake bug, PDF libraries install cleanly, and
`systemd` restarts everything on boot with no login. You manage it over SSH from
your main laptop and never look at that broken screen again.

Father's laptop stays **Windows** (Tally only runs on Windows) - nothing about
his side changes.

## 1. Copy the app onto the i3
From your main laptop, or download on the i3:
```bash
mkdir -p ~/asva && cd ~/asva
unzip /path/to/ASVA_server.zip -d .
```

## 2. Run the installer (once)
```bash
cd ~/asva
bash deploy/linux/setup_asva.sh
```
It installs Python + Node + the WeasyPrint PDF libraries, builds the venv,
installs the WhatsApp service, and registers two services that start on boot and
restart themselves:
- **asva-backend** - API + scheduler + Command Center + downloads (port 8000)
- **asva-bot** - the owner-assistant WhatsApp, your number (port 3002)

Check it: `curl http://localhost:8000/health` -> `{"status":"ok", ...}`.

## 3. Publish the shop download
```bash
mkdir -p ~/asva/downloads
cp /path/to/ASVA_shop.zip ~/asva/downloads/
```
(`downloads_dir` defaults to `downloads` relative to the app, i.e. `~/asva/downloads`.)

## 4. Scan the bot WhatsApp
Open `http://localhost:3002/qr` on the i3 (or tunnel it) and scan with the phone
holding **9344110272**.

## 5. Cloudflare Tunnel (public address for the app)
Same dashboard-managed tunnel as the Windows guide, Linux connector:
1. Cloudflare -> **Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared** -> name `asva`.
2. Choose **Debian/Ubuntu (64-bit)**; copy the install command (it ends in a token) and run it on the i3. It installs `cloudflared` as its own service.
3. **Public Hostnames -> Add**:
   - `app` . `tryasva.com` -> HTTP -> `localhost:8000`
   - `link` . `tryasva.com` -> HTTP -> `localhost:3002`
4. Confirm: `https://app.tryasva.com/health` works from anywhere.

The website (`tryasva.com`) still goes on Cloudflare Pages from `ASVA_website.zip`
- unchanged (Guide 2 in `HOST_SETUP.md`).

## Everyday commands
```bash
systemctl status asva-backend asva-bot     # are they up?
journalctl -u asva-backend -f              # live backend logs
journalctl -u asva-bot -f                  # live bot logs
sudo systemctl restart asva-backend        # restart after an update
```

## Shipping an update to the host
```bash
cd ~/asva
unzip -o /path/to/ASVA_server.zip -d .     # overwrite code (keeps .env)
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart asva-backend asva-bot
```
Shops (father) still auto-update themselves via `updater.py` from the zip you
drop in `~/asva/downloads` - unchanged.

## Manage from your main laptop (SSH)
On the i3 once: `sudo apt install -y openssh-server`. Then from your laptop:
`ssh <user>@<i3-ip>`. Everything above runs over that connection - the broken
screen is never needed again.
