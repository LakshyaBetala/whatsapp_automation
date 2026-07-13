# ASVA HOST SETUP (the always-on i3 laptop = the server)

This laptop is the **server**. It runs the backend, the scheduler (reminders +
digest), the Command Center, and the shop's WhatsApp session. Because the shop's
WhatsApp lives here, reminders go out even on Sundays when the shop's own laptop
is off. Each shop laptop runs only the thin Tally agent and points at this host.

Your i3 has a broken screen, so this guide sets it up to run **headless** - you
do everything from your main laptop. You do this once. After that the host just
stays on and you watch it from `api.tryasva.com/ops`.

---

## The domain map (one domain, tryasva.com does everything)

| URL | What |
|---|---|
| `tryasva.com` / `www.tryasva.com` | your landing / marketing page (separate, Cloudflare Pages) |
| `api.tryasva.com` | the backend on this i3 host (via Cloudflare Tunnel) |
| `api.tryasva.com/ops` | the Command Center (health + subscriptions + Add business) |
| `link.tryasva.com` | the WhatsApp QR page, to link a shop from your own laptop |

---

## 0. Control the headless i3 from your main laptop

Set this up FIRST so the broken screen never matters again.

1. On the i3 (while the screen is still partly usable, or plug in an external
   monitor once), install **Chrome Remote Desktop**:
   `https://remotedesktop.google.com/access` -> "Set up remote access". Sign in
   with your Google account, set a name and a PIN.
2. On your main laptop, open the same page and sign in with the same account.
   The i3 appears - click it, enter the PIN, and you now see the i3's screen in
   a browser tab. Everything below you do through that tab.
3. Set the i3 to **log in automatically** (so it comes back after a power cut):
   `Win+R` -> `netplwiz` -> untick "Users must enter a user name and password".

(Chrome Remote Desktop is free, needs no port setup, and reconnects on its own.
Windows Remote Desktop also works if you prefer it.)

## 1. Put ASVA on the host

1. Unzip `ASVA_server.zip` to `C:\ASVA`.
2. In PowerShell in `C:\ASVA`, set up the backend once:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Set up the WhatsApp service once:
   ```powershell
   cd wa_service ; npm install ; cd ..
   ```
4. Open `.env` - the build already set `ADMIN_API_KEY` (your Command Center key,
   keep it secret), `PUBLIC_BASE_URL=https://api.tryasva.com`, the scheduler ON,
   and `SEND_VIA_OUTBOX=false`. Set one thing: `OPERATOR_UPI_ID=yourvpa@bank`
   (and `OPERATOR_UPI_NAME=ASVA`). Then renewal reminders carry the amount +
   your UPI + a tap-to-pay link, so shops pay you directly; you confirm and
   click **Renew** in the Command Center.

## 2. Never let it sleep

Right-click `KEEP_AWAKE.bat` -> **Run as administrator**. Stops the laptop
sleeping/hibernating and keeps it running with the lid closed. Keep it plugged in.

## 3. The tunnel: point api.tryasva.com at this host (free)

1. Download `cloudflared-windows-amd64.exe` from Cloudflare, rename it to
   `cloudflared.exe`, put it in `C:\ASVA`.
2. Move tryasva.com onto Cloudflare: add the site in the Cloudflare dashboard
   (free plan) and change the domain's nameservers at your registrar to the two
   Cloudflare gives you. Wait until Cloudflare shows the domain "Active".
3. In PowerShell in `C:\ASVA`:
   ```powershell
   .\cloudflared.exe tunnel login
   .\cloudflared.exe tunnel create asva
   .\cloudflared.exe tunnel route dns asva api.tryasva.com
   .\cloudflared.exe tunnel route dns asva link.tryasva.com
   ```
4. Create `C:\Users\<you>\.cloudflared\config.yml` (use the credentials
   filename the `create` step printed):
   ```yaml
   tunnel: asva
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: api.tryasva.com
       service: http://localhost:8000
     - hostname: link.tryasva.com
       service: http://localhost:3001
     - service: http_status:404
   ```

## 4. Start everything

- Double-click **`HOST_START.bat`** -> backend (8000) + shop WhatsApp (3001).
- Double-click **`TUNNEL.bat`** -> `api.tryasva.com` goes live.

Link the shop's WhatsApp (once): from your **main laptop or phone**, open
`https://link.tryasva.com/qr` and scan it with the **shop owner's** phone. That
session now stays online on the host, so Sunday reminders send.

## 5. Autostart on boot (so a power cut self-heals)

1. On the i3: `Win+R` -> `shell:startup` -> Enter.
2. Put shortcuts to `HOST_START.bat` and `TUNNEL.bat` in that folder.

Combined with auto-login (step 0.3) and Keep Awake, a reboot brings the whole
server back with no screen needed.

## 6. Your control panel

From any browser (main laptop, phone):
```
https://api.tryasva.com/ops?key=YOUR_ADMIN_API_KEY
```
Every shop at a glance: online/offline, plan, days to expiry, messages this
month, version, failed sends. **+ Add business**, **Renew**, **Suspend**, change
plan. This is where you run access for every business.

Health check:
- `https://api.tryasva.com/health` -> ok.
- `https://link.tryasva.com/qr` -> "connected".
- A shop shows "online" in `/ops` within a minute of running its agent.

---

## Onboarding a shop

1. In `/ops`, click **+ Add business**: shop name, owner, WhatsApp number, plan,
   months paid.
2. It shows a **licence key** + secret **agent token** + a ready `config.json`
   (already using `https://api.tryasva.com`). Copy it.
3. On the shop's laptop, unzip `ASVA_shop_client.zip`, follow `SHOP_AGENT_SETUP.md`,
   paste the config, run `AGENT_ONLY.bat`. Live.

Access is enforced server-side: every send is checked against the subscription
here before it goes. No pay -> 3-day grace -> sends stop until you Renew. The
shop laptop cannot bypass it.

---

## No domain moved onto Cloudflare yet? (trial in 2 minutes)

Skip step 3 and run:
```powershell
.\cloudflared.exe tunnel --url http://localhost:8000
```
It prints a random `https://xxxx.trycloudflare.com` URL - use it as the shop's
`backend_url` and your `/ops` address to test. It changes every restart, so
switch to `api.tryasva.com` before a real shop goes on it.
