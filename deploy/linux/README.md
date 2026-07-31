# ASVA host on Ubuntu - complete guide

Everything to run ASVA on the i3 (Ubuntu) and operate it day to day. Ubuntu is
the better home for the host: no lock screen, no display-wake bug, PDF libraries
install cleanly, and `systemd` starts and restarts everything on boot with no
login. You run it headless and manage it over SSH from your main laptop.

**Father's laptop stays Windows** (Tally only runs on Windows). Nothing on his
side changes. This guide is only the i3 host + the website.

---

## What runs where

| Piece | Where | Port / address |
|---|---|---|
| Website (marketing, SEO) | Cloudflare Pages (free) | `tryasva.com` |
| App: API + Command Center + downloads | i3, `asva-backend` service | `localhost:8000` → `app.tryasva.com` |
| Bot WhatsApp (owner assistant, your number 9344110272) | i3, `asva-bot` service | `localhost:3002` → `link.tryasva.com` |
| Public address for the app | Cloudflare Tunnel (`cloudflared` service) | `app.tryasva.com` |
| Shop app (Tally + shop WhatsApp) | father's Windows laptop | `localhost:3001` |

---

## PART 1 - Install the host (once)

### 1. Put the app on the i3
```bash
mkdir -p ~/asva && cd ~/asva
unzip /path/to/ASVA_server.zip -d .
```

### 2. Run the installer
```bash
bash deploy/linux/setup_asva.sh
```
It installs Python + Node + the WeasyPrint PDF libraries, builds the venv,
installs the WhatsApp service, and registers two auto-restarting services:
- **asva-backend** - API, scheduler, Command Center, downloads (:8000)
- **asva-bot** - the owner-assistant WhatsApp, your number (:3002)

At the end it prints your Command Center URL (with the admin key) and a health
line. Confirm: `curl http://localhost:8000/health` → `{"status":"ok", ...}`.

### 3. Publish the shop download
```bash
cp /path/to/ASVA_shop.zip ~/asva/downloads/
```

### 4. Scan the bot WhatsApp
Open `http://localhost:3002/qr` (on the i3, or over SSH tunnel) and scan with the
phone holding **9344110272**.

### 5. Cloudflare Tunnel - the app's public address
1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared**, name it `asva`.
2. Pick **Debian/Ubuntu (64-bit)**; copy the install command (ends in a token) and run it on the i3. It installs `cloudflared` as its own service.
3. **Public Hostnames → Add**, twice:
   - `app` . `tryasva.com` → HTTP → `localhost:8000`
   - `link` . `tryasva.com` → HTTP → `localhost:3002`
4. Confirm `https://app.tryasva.com/health` works from your phone.

### 6. Lock the Command Center
Open only at `https://app.tryasva.com/ops?key=<ADMIN_API_KEY>` (the key is the
`ADMIN_API_KEY` line in `~/asva/.env`, also printed by the installer). Recommended:
Cloudflare **Zero Trust → Access** → application for `app.tryasva.com/ops*`
allowing only your email.

### 7. The website (`tryasva.com`)
Unchanged from the main guide: upload `ASVA_website.zip` to **Cloudflare Pages**
and add the custom domain `tryasva.com`. See `HOST_SETUP.md` Guide 2.

---

## PART 2 - How each piece works, and how to keep it ready

### Add a business (onboard a shop)
Command Center → **+ Add business** → shop name, owner, the shop's 10-digit
WhatsApp, plan, paid months → **Create business**. You get a **Download link**
(personal, token-gated), the agent token, and a ready config. Send the shop the
Download link; they paste the config, run the app, and scan their own WhatsApp.
Their business is now visible in your Command Center.

### The bot (owner assistant)
The `asva-bot` service is your number, 9344110272. Owners message it; it never
messages their customers. Available on **Growth and above** (a Basic owner is
told it's a Growth feature; their bills/reminders keep working). Commands:
`HELP`, `LIST`, `CHECK <party>`, `REMIND <party>`, `BILL <party>`, `PAID`.

### Health monitoring
Command Center → **Health** tab shows it live (refreshes every 30s): server / DB
/ bot-WhatsApp / email status, each scheduled job's last run, per-shop
sent/failed/blocked/queued, 14-day traffic, and open alerts. Under the hood a
**watchdog job runs every 5 minutes**: it builds a health snapshot, opens an
alert the first time something is wrong, and resolves it when it clears.

**Email alerts** (get told the moment something critical drops): add these to
`~/asva/.env`, then `sudo systemctl restart asva-backend`. Gmail needs an **app
password**, not your normal password:
```
ALERT_EMAIL_TO=almmatix@gmail.com
ALERT_EMAIL_FROM=almmatix@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=almmatix@gmail.com
SMTP_PASS=your-16-char-gmail-app-password
```

### Subscription cycle + payments
Two separate "payments":
- **Customer → shop** (the shop's own receivables): automatic. A receipt in Tally
  marks that customer's bills paid. Nothing for you to do.
- **Shop → you** (their ASVA subscription): direct UPI, you confirm and renew.
  1. **Add business** starts a **30-day** cycle.
  2. Near expiry the owner gets a WhatsApp renewal notice with the amount and a
     tap-to-pay link to your UPI **9344110272@ybl**.
  3. They pay you; you see it in your UPI app.
  4. Command Center → **+1 mo** on that shop. Expiry extends and, if they were cut
     off, **sends resume automatically** (status is recomputed live on every send).
  5. **Grace** 3 days after expiry (owner warned), then **suspended**. The
     **Suspend** button cuts a shop off now; **Renew** reverses it. The plan
     dropdown changes tier without touching expiry.

### How auto-update works
- **Shops (father) update themselves.** Every launch, the shop's `START.bat` runs
  `updater.py`: it asks `app.tryasva.com/license/status` whether a newer version
  exists and, if so, downloads `ASVA_shop.zip` (with its own token) and applies it
  **keeping the shop's .env, config, and WhatsApp login**. To ship an update to
  every shop: rebuild `ASVA_shop.zip`, drop it in `~/asva/downloads/`, and insert a
  new row in the `app_releases` table (Supabase) with the new version. Each shop
  picks it up on its next launch; the Command Center version flips and the
  **Outdated** count drops.
- **The host (this i3) you update yourself** (you control the server):
  ```bash
  bash deploy/linux/update_asva.sh /path/to/new/ASVA_server.zip
  ```
  It overwrites the code, keeps your `.env`/`downloads`/WhatsApp session,
  reinstalls dependencies, and restarts the services.

---

## Everyday commands
```bash
bash deploy/linux/asva_status.sh          # one-glance status (services, health, bot, tunnel)
systemctl status asva-backend asva-bot    # service detail
journalctl -u asva-backend -f             # live backend logs
journalctl -u asva-bot -f                 # live bot logs
sudo systemctl restart asva-backend       # restart after an .env change
```

## Manage from your main laptop (SSH)
On the i3 once: `sudo apt install -y openssh-server`. Then from your laptop
`ssh <user>@<i3-ip>` and run everything above. You never need the broken screen.

## Troubleshooting
- **Backend down after boot:** `journalctl -u asva-backend -n 80 --no-pager` (usually a `.env` typo or Supabase unreachable).
- **Bot shows disconnected:** open `http://localhost:3002/qr` and rescan with 9344110272 (WhatsApp drops a session if the phone is offline ~14 days).
- **A shop shows offline in Health:** their laptop or the shop app is off; its bills/reminders resume when it's back.
- **`app.tryasva.com` unreachable but `localhost:8000` works:** the tunnel is down - `systemctl status cloudflared`, or re-run the connector install command.
