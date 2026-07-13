# ASVA HOST SETUP (the always-on i3 laptop)

This laptop is the **server**. It runs the backend, the scheduler (reminders +
digest), the Command Center, and the shop's WhatsApp session. Because the shop's
WhatsApp lives here, reminders go out even on Sundays when the shop's own laptop
is off. Each shop laptop runs only the thin Tally agent and points at this host.

You do this **once**. After that the host just stays on.

---

## What you need

- The i3 laptop, plugged in, on your home/office internet.
- Python 3.11+ and Node 18+ installed.
- A domain name on Cloudflare (about Rs 800/year). Needed for a **stable**
  public URL. No domain yet? See "Quick test URL" at the bottom to trial it free.

---

## 1. Put ASVA on the host

1. Unzip `ASVA_server.zip` to `C:\ASVA`.
2. Open PowerShell in `C:\ASVA` and set up the backend once:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Set up the WhatsApp service once:
   ```powershell
   cd wa_service
   npm install
   cd ..
   ```
4. Open `.env` and confirm these (the build already set them):
   - `ADMIN_API_KEY=` is filled in (this is your Command Center key - keep it secret).
   - `ENABLE_REMINDER_SWEEP=true`, `ENABLE_EOD_DIGEST=true`.
   - `SEND_VIA_OUTBOX=false` (WhatsApp is here, so it sends directly).
   - After step 3 below, set `PUBLIC_BASE_URL=https://asva.YOURDOMAIN.com`.

## 2. Never let it sleep

Right-click `KEEP_AWAKE.bat` -> **Run as administrator**. This stops the laptop
sleeping or hibernating, and keeps it running with the lid closed. Keep it plugged in.

## 3. Give the host a public URL (Cloudflare Tunnel, free)

1. Install cloudflared: download `cloudflared-windows-amd64.exe` from Cloudflare,
   rename it to `cloudflared.exe`, put it in `C:\ASVA`.
2. Add your domain to Cloudflare (free plan) and point its nameservers to Cloudflare.
3. In PowerShell in `C:\ASVA`:
   ```powershell
   .\cloudflared.exe tunnel login
   .\cloudflared.exe tunnel create asva
   .\cloudflared.exe tunnel route dns asva asva.YOURDOMAIN.com
   ```
4. Create `C:\Users\<you>\.cloudflared\config.yml`:
   ```yaml
   tunnel: asva
   credentials-file: C:\Users\<you>\.cloudflared\asva.json
   ingress:
     - hostname: asva.YOURDOMAIN.com
       service: http://localhost:8000
     - service: http_status:404
   ```
   (The `create` step printed the real credentials filename - use that.)
5. Put `https://asva.YOURDOMAIN.com` into `.env` as `PUBLIC_BASE_URL` and save.

## 4. Start everything

- Double-click **`HOST_START.bat`** -> backend (8000) + shop WhatsApp (3001).
- Double-click **`TUNNEL.bat`** -> the public URL.

First time only, link the shop's WhatsApp: open `http://localhost:3001/qr` in a
browser on the host and scan it with the **shop owner's** phone. That session
now stays online here 24/7, so Sunday reminders send.

## 5. Autostart on boot

So the host recovers after a power cut, add both to Startup:

1. Press `Win + R`, type `shell:startup`, Enter.
2. Put shortcuts to `HOST_START.bat` and `TUNNEL.bat` in that folder.

Now a reboot brings the whole server back on its own.

## 6. Open the Command Center

From any browser (yours, your phone):
```
https://asva.YOURDOMAIN.com/ops?key=YOUR_ADMIN_API_KEY
```
You see every shop: online/offline, plan, days to expiry, messages this month,
version, failed sends. From here you **Add business**, **Renew**, **Suspend**,
or change a plan. This is your control panel for access to every business.

---

## Onboarding a shop (from the Command Center)

1. Click **+ Add business**. Enter the shop name, owner name, the shop's
   WhatsApp number, the plan, and how many months they paid.
2. It shows a **licence key** and a secret **agent token**, plus a ready
   `config.json`. Copy that config.
3. On the shop's laptop, unzip `ASVA_shop_client.zip`, open `SHOP_AGENT_SETUP.md`,
   paste the config, and run `AGENT_ONLY.bat`. That shop is now live.

Access control is server-side: every send is checked against the subscription
here before it goes. If a shop does not pay, it drops to a 3-day grace and then
sends stop automatically until you Renew. The shop laptop cannot bypass this.

---

## Quick test URL (no domain, for trying it out)

Skip step 3. Instead run:
```powershell
.\cloudflared.exe tunnel --url http://localhost:8000
```
It prints a random `https://something.trycloudflare.com` URL. Use that as
`backend_url` on the shop and as your `/ops` address. It **changes every time**
you restart the tunnel, so it is only for testing - buy the domain before you
put a real shop on it.

## Health check

- Backend up: open `https://asva.YOURDOMAIN.com/health` -> should say ok.
- WhatsApp linked: `http://localhost:3001/qr` on the host shows "connected".
- A shop is reporting in: it appears "online" in `/ops` within a minute of
  running its agent.
