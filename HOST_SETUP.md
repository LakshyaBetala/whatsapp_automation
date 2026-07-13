# ASVA HOST SETUP (the always-on i3 laptop = server + bot)

This laptop is the **server + bot**. It runs the backend, the scheduler
(decides WHEN to remind), the Command Center, and the **bot** WhatsApp (your
number, for owner digests/alerts/commands). It never messages customers.

The **shop's** WhatsApp is scanned on the **shop's own laptop** by the
shopkeeper, and customer messages go out from the shop's number. The server
decides the timing and queues each message; the shop laptop delivers it when it
is on. So the customer only ever hears from the shop, and the timing is reliable
because the i3 is always on.

Your i3 has a broken screen, so this runs **headless** - you do everything from
your main laptop. One-time setup, then it stays on.

---

## Who runs what

| Machine | Runs | WhatsApp scanned by |
|---|---|---|
| **i3 host** (always on) | backend + scheduler + Command Center + **bot** WhatsApp | **you** (your number), on the i3 |
| **Shop laptop** (father's) | Tally agent + **shop** WhatsApp + delivers queued sends | **the shopkeeper** (their number), on their laptop |

## The domain map (tryasva.com does everything)

| URL | What |
|---|---|
| `tryasva.com` / `www` | landing page (later, Cloudflare Pages) |
| `api.tryasva.com` | the backend + `/ops` Command Center (via Cloudflare Tunnel) |
| `link.tryasva.com` | the **bot** QR, so you scan the bot from your phone |

The shop's QR is NOT on the internet - it stays at `localhost:3001/qr` on the
shop's laptop and the shopkeeper scans it there.

---

## 0. Control the headless i3 from your main laptop

1. On the i3, install **Chrome Remote Desktop**
   (`remotedesktop.google.com/access` -> "Set up remote access"), sign in with
   your Google account, set a PIN. From your main laptop, open the same page and
   connect. You now see the i3 in a browser tab.
2. Auto-login after a power cut: `Win+R` -> `netplwiz` -> untick "Users must
   enter a user name and password".

## 1. Put ASVA on the host

1. Unzip `ASVA_server.zip` to `C:\ASVA`.
2. Backend once: `python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt`
3. WhatsApp once: `cd wa_service ; npm install ; cd ..`
4. `.env` is already set (Command Center key, `api.tryasva.com`, scheduler ON,
   `SEND_VIA_OUTBOX=true`, bot on `:3002`, monitor ON). Fill in two things:
   - `OPERATOR_UPI_ID=yourvpa@bank` so renewal reminders carry your UPI.
   - Email alerts (so a drop mails you): `ALERT_EMAIL_TO=you@gmail.com`,
     `ALERT_EMAIL_FROM=you@gmail.com`, `SMTP_HOST=smtp.gmail.com`,
     `SMTP_USER=you@gmail.com`, `SMTP_PASS=<Gmail APP password>` (make one at
     myaccount.google.com -> Security -> App passwords; not your real password).
     Leave blank to skip email - alerts still show in the Health tab.

## 2. Never let it sleep

Right-click `KEEP_AWAKE.bat` -> **Run as administrator**.

## 3. Move tryasva.com DNS to Cloudflare (keep the Vercel landing) + tunnel

The **root** tryasva.com is a landing page on **Vercel**. We keep that working
and only add two subdomains for the i3. Cloudflare runs the DNS; GoDaddy stays
the registrar; Vercel keeps serving the landing.

1. Create a free `cloudflare.com` account -> **Add a site** -> `tryasva.com`
   -> Free plan. Cloudflare **scans your existing DNS** - let it import
   everything (this preserves the Vercel records). It gives you **two
   nameservers** (like `xxx.ns.cloudflare.com`).
2. In **GoDaddy**: domain -> **Nameservers** -> **Change** -> **Enter my own** ->
   paste the two Cloudflare ones -> Save. Cloudflare emails you when "Active".
3. In **Cloudflare -> DNS**, confirm the landing still points to Vercel (if the
   import missed it, add: apex `tryasva.com` **A** -> `76.76.21.21`, and `www`
   **CNAME** -> `cname.vercel-dns.com`, both DNS-only / grey cloud). Open
   `tryasva.com` to confirm the landing still loads. THEN continue - the tunnel
   only adds `api.` and `link.`, it never touches the root.
4. On the i3, download `cloudflared` (rename to `cloudflared.exe`, put in
   `C:\ASVA`), then:
   ```powershell
   .\cloudflared.exe tunnel login
   .\cloudflared.exe tunnel create asva
   .\cloudflared.exe tunnel route dns asva api.tryasva.com
   .\cloudflared.exe tunnel route dns asva link.tryasva.com
   ```
5. Create `C:\Users\<you>\.cloudflared\config.yml`:
   ```yaml
   tunnel: asva
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: api.tryasva.com
       service: http://localhost:8000
     - hostname: link.tryasva.com
       service: http://localhost:3002
     - service: http_status:404
   ```

## 4. Lock it down (so only YOU can reach the Command Center)

Owning the domain means no one else can use `tryasva.com`. To stop anyone
reaching your control panel, add **Cloudflare Access** (free, up to 50 users):

1. Cloudflare dashboard -> **Zero Trust** -> **Access** -> **Applications** ->
   **Add** -> Self-hosted.
2. Application domain: `api.tryasva.com`, path `/ops`. Add a second app for
   path `/license`.
3. Policy: **Allow**, rule **Emails** = your Google email (add trusted teammate
   emails later). Save.

Now `api.tryasva.com/ops` first asks for a Cloudflare login and only lets your
email through - even before the admin key. Two locks: Cloudflare Access (who can
reach it) + the admin key (what acts). The shop agents hit `/tally/*` and
`/license/heartbeat`, which stay open but require a valid **agent token**, so a
stranger with the URL still cannot do anything.

## 5. Start + link the bot

- Double-click **`HOST_START.bat`** -> backend (8000) + bot WhatsApp (3002).
- Double-click **`TUNNEL.bat`**.
- Scan the **bot** once: from your phone open `https://link.tryasva.com/qr` and
  scan with **your** WhatsApp. This is the only QR you ever scan.

## 6. Autostart on boot

On the i3: `Win+R` -> `shell:startup` -> put shortcuts to `HOST_START.bat` and
`TUNNEL.bat` there. With auto-login + Keep Awake, a reboot self-heals.

## 7. Your control panel

`https://api.tryasva.com/ops` (Cloudflare asks your email, then the key is in
the URL you bookmark). See every shop: online/offline, plan, expiry, messages,
version, failed sends. **+ Add business**, **Renew**, **Suspend**, change plan.

---

## Onboard the shop (father's laptop)

1. In `/ops` click **+ Add business**: shop name, owner, the shop's WhatsApp
   number, plan, months paid. It shows a licence key + agent token + a ready
   `config.json`.
2. On the shop's laptop: unzip **`ASVA_shop.zip`**, paste the `agent_token` +
   `business_id` into `tally_agent\config.json` (set `backend_url` to
   `http://localhost:8000` - the shop runs its own local backend), set the Tally
   `company_name`, then run `SETUP.bat` once and `START.bat` daily.
3. On the shop's laptop, open `http://localhost:3001/qr` and have the
   **shopkeeper** scan with the **shop's** WhatsApp. That number now sends the
   bills and reminders. It stays linked as long as the shopkeeper opens WhatsApp
   on their phone within any 14-day window (they use it daily, so it holds).

Access is enforced on the server: every send is checked against the subscription
before it goes. No pay -> 3-day grace -> sends stop until you Renew.

---

## No domain live yet? (trial in 2 minutes)

On the i3: `.\cloudflared.exe tunnel --url http://localhost:8000` prints a random
`https://xxxx.trycloudflare.com`. Use it as `/ops` and the shop `backend_url` to
test; it changes each restart, so switch to `api.tryasva.com` before a real shop.
