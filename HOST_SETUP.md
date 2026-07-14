# ASVA SERVER SETUP - the complete guide (i3 host + tryasva.com)

This sets up ONE always-on laptop (your i3) as the whole ASVA server: the public
landing page, the backend + scheduler, the Command Center health dashboard, and
the owner bot. Then each shop (starting with father's) runs a thin app that reads
Tally and sends from the shop's own WhatsApp.

Everything lives on the i3 and is reached through your domain `tryasva.com` over a
free Cloudflare Tunnel. Your i3 has a broken screen, so this runs headless, you
do it all from your main laptop.

---

## The map (one i3, one domain)

| URL | Serves | Who reaches it |
|---|---|---|
| `tryasva.com` , `www.tryasva.com` | the ASVA **landing page** + the API | public |
| `tryasva.com/ops` | the **Command Center** (health + subscriptions) | you only (locked) |
| `tryasva.com/admin?token=...` | a shop owner's own dashboard | that shop owner |
| `link.tryasva.com` | the **bot** WhatsApp QR | you (scan once) |

All of that is one backend on the i3 (port 8000) plus the bot WhatsApp (port 3002).
The shop's own WhatsApp is NOT here, it stays on the shop's laptop.

---

## PART A - the i3 host (do this today)

### A0. Reach the headless i3 from your main laptop
1. On the i3, install **Chrome Remote Desktop** (`remotedesktop.google.com/access`
   -> "Set up remote access"), sign in, set a PIN. Connect to it from your main
   laptop's browser. Everything below is done through that.
2. Auto-login after a power cut: `Win+R` -> `netplwiz` -> untick "Users must
   enter a user name and password".

### A1. Install ASVA
1. Unzip **`ASVA_server.zip`** to `C:\ASVA`.
2. Backend (once): `python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt`
3. WhatsApp service (once): `cd wa_service ; npm install ; cd ..`
4. Open `.env` and fill two things (the rest is already set):
   - `OPERATOR_UPI_ID=yourvpa@bank` - your UPI, so renewal reminders carry it.
   - Email alerts: `ALERT_EMAIL_TO=you@gmail.com`, `ALERT_EMAIL_FROM=you@gmail.com`,
     `SMTP_HOST=smtp.gmail.com`, `SMTP_USER=you@gmail.com`,
     `SMTP_PASS=<Gmail App password>` (make one at myaccount.google.com ->
     Security -> App passwords). Blank = alerts still show in /ops, just not mailed.
   - `PUBLIC_BASE_URL=https://tryasva.com` and `ADMIN_API_KEY=...` are already set.

### A2. Never sleep
Right-click **`KEEP_AWAKE.bat`** -> Run as administrator.

### A3. Move tryasva.com to Cloudflare (off Vercel) + open the tunnel
The domain is at GoDaddy and the old page is on Vercel. We move DNS to Cloudflare
and point the whole domain at the i3. The old Vercel page simply stops receiving
traffic (you can delete that Vercel project later, it is not needed).

1. Create a free `cloudflare.com` account -> **Add a site** -> `tryasva.com` ->
   Free plan. Let it import existing records. It gives you **two nameservers**.
2. In **GoDaddy**: domain -> **Nameservers** -> **Change** -> **Enter my own** ->
   paste the two Cloudflare ones -> Save. Wait for Cloudflare to say "Active".
3. In **Cloudflare -> DNS**, DELETE any old Vercel records for `@` and `www`
   (A record to 76.76.21.21, or CNAME to vercel-dns) - the tunnel will own these.
4. On the i3, download `cloudflared` (rename to `cloudflared.exe`, put in `C:\ASVA`):
   ```powershell
   .\cloudflared.exe tunnel login
   .\cloudflared.exe tunnel create asva
   .\cloudflared.exe tunnel route dns asva tryasva.com
   .\cloudflared.exe tunnel route dns asva www.tryasva.com
   .\cloudflared.exe tunnel route dns asva link.tryasva.com
   ```
5. Create `C:\Users\<you>\.cloudflared\config.yml`:
   ```yaml
   tunnel: asva
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: tryasva.com
       service: http://localhost:8000
     - hostname: www.tryasva.com
       service: http://localhost:8000
     - hostname: link.tryasva.com
       service: http://localhost:3002
     - service: http_status:404
   ```

### A4. Lock the Command Center to you (Cloudflare Access, free)
So only you can open the health dashboard, even with the URL:
1. Cloudflare -> **Zero Trust** -> **Access** -> **Applications** -> Add ->
   Self-hosted. Domain `tryasva.com`, **path `/ops`**.
2. Policy: **Allow**, rule **Emails** = your Google email. Save.
Now `tryasva.com/ops` asks for your Cloudflare login first, then the admin key in
the URL. The landing page (`/`) and the agent endpoints stay public, so shops
still work; only `/ops` is gated.

### A5. Start + link the bot
- Double-click **`HOST_START.bat`** -> backend (8000) + bot WhatsApp (3002).
- Double-click **`TUNNEL.bat`**.
- From your phone open `https://link.tryasva.com/qr` and scan with **your**
  WhatsApp. That is the only QR you ever scan.

### A6. Autostart on boot
On the i3: `Win+R` -> `shell:startup` -> put shortcuts to `HOST_START.bat` and
`TUNNEL.bat` there. With auto-login + Keep Awake, a reboot self-heals.

### A7. Check it
- `https://tryasva.com` -> the ASVA landing page loads (public).
- `https://tryasva.com/ops` -> Cloudflare login (your email) -> Command Center,
  Health tab. Bookmark `https://tryasva.com/ops?key=YOUR_ADMIN_API_KEY`.
- `https://link.tryasva.com/qr` -> "connected".

---

## PART B - father's shop (do this tomorrow)

### B1. Create the shop in the Command Center
Open `tryasva.com/ops` -> **+ Add business** -> shop name, owner, the shop's
WhatsApp number, plan, months paid. It shows a licence key + agent token + a ready
`config.json`. Copy it.

### B2. Set up the shop app
1. On father's laptop, quit the old standalone ASVA.
2. Unzip **`ASVA_shop.zip`** to `C:\ASVA`.
3. Paste `agent_token` + `business_id` into `tally_agent\config.json`, keep
   `backend_url` = `https://tryasva.com`, set the Tally `company_name`.
   (To keep his WhatsApp linked, copy the old `wa_service\.baileys_auth` folder in.)
4. `SETUP.bat` once, then `START.bat` daily.
5. Open `localhost:3001/qr` on his laptop and have **father** scan with the
   **shop's** WhatsApp. That number sends the bills and reminders.
6. Load the Tally button: copy `ASVA_SendBill.tdl` to `C:\ASVA`, then TallyPrime
   `F1 -> TDL & Add-On -> F4 -> Load on Startup: Yes`, add the path, restart Tally.

### B3. Confirm
In `tryasva.com/ops` the shop shows **online** within a minute, its WhatsApp shows
connected, and a test bill (Send to ASVA in Tally) reaches the customer.

---

## How the pieces talk (so you know what runs where)
- The **i3** decides WHEN to remind (scheduler), queues each customer message,
  serves the landing + Command Center, and runs the owner bot. Always on.
- The **shop laptop** reads Tally, and delivers the queued messages from the
  shop's own WhatsApp when it is on. The customer only ever hears the shop.
- Every send is checked against the subscription on the i3 before it goes. No
  pay -> 3-day grace -> sends stop until you Renew in `/ops`. The shop cannot
  bypass this.
- If anything drops (a shop offline, its WhatsApp down, the bot down, a stuck
  queue), the watchdog on the i3 emails you within minutes.

## No domain live yet? (trial in 2 minutes)
On the i3: `.\cloudflared.exe tunnel --url http://localhost:8000` prints a random
`https://xxxx.trycloudflare.com`. Use it as `/ops` and the shop `backend_url` to
test; switch to `tryasva.com` before real use.
