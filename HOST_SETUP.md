# ASVA - Deploy & Operate Guide

Everything to take ASVA live and run it day to day. Follow the parts in order the
first time. After that, the short sections (add a business, ship an update,
health, subscriptions) are what you come back to.

## The map (who runs what)

Three machines, two of them yours:

| Machine | Runs | WhatsApp | Always on? |
|---|---|---|---|
| **i3 host** | backend `:8000` + scheduler + Command Center + landing + the **bot** WhatsApp `:3002` | **9344110272** (ASVA's own number) | yes |
| **Shop laptop** (your father's) | Tally + a small ASVA app + the **shop** WhatsApp `:3001` | **9444294894** (the shop's own number) | only when the shop is open |
| **Your laptop** | just a browser to open the Command Center | none | no |

Rules that never change:
- A **customer** only ever hears from the **shop's own number**. The bot number is for the **owner** only.
- The **bot** WhatsApp is scanned once on the **i3** (by you). Each **shop** WhatsApp is scanned on **that shop's laptop** (by the shopkeeper). This is why WhatsApp does not keep disconnecting: the number stays on the phone that owns it.
- The **operator admin key** and the Supabase service key live **only on the i3 and your browser**, never on a shop laptop.

---

## Part A - Host the server on the i3 (do once)

### A1. Get onto the i3
The i3 has a half-broken screen, so set up remote control once (optional but handy):
install **Chrome Remote Desktop** on the i3 and on your laptop, sign in with the
same Google account, and you can drive the i3 from your laptop.

### A2. Install and start ASVA
1. Copy **`ASVA_server.zip`** to the i3 and unzip it to **`C:\ASVA`** (so you have `C:\ASVA\ASVA_HOST.bat`).
2. Double-click **`ASVA_HOST.bat`**. The first run installs Python and Node dependencies (2 to 3 minutes), then opens two windows and keeps them alive:
   - **Backend** (port 8000): API, scheduler, Command Center, landing.
   - **Bot WhatsApp** (port 3002): the owner assistant number.
3. Leave both windows open. Close only the small launcher window.

Quick check: on the i3 open `http://localhost:8000/health`. You want `{"status":"ok", ...}`.

### A3. Never sleep
Right-click **`KEEP_AWAKE.bat`** -> **Run as administrator**. An always-on server
must not sleep or the scheduler and WhatsApp stop. Keep the i3 plugged in. The
screen turning off is fine.

### A4. Publish the downloadable app
So `tryasva.com/download` and auto-update have something to serve:
1. Make the folder **`C:\ASVA\downloads`**.
2. Copy **`ASVA_shop.zip`** into it. Whenever you ship an update (Part G), you replace this file.

### A5. Point tryasva.com at the i3 (Cloudflare Tunnel)
This gives the i3 a public HTTPS address with no port-forwarding. You need your
Cloudflare and GoDaddy logins for this part. (Say the word and I will walk you
through each screen live.)

1. **Cloudflare account**: sign up (free) at cloudflare.com, **Add a site** -> `tryasva.com`, pick the Free plan. Cloudflare shows you two nameservers.
2. **GoDaddy**: in your GoDaddy domain settings, change the nameservers to the two Cloudflare gave you. (Remove the old Vercel A/CNAME records for the landing; the tunnel replaces them.) This can take a few minutes to a few hours.
3. **Install cloudflared** on the i3 (download the Windows build from Cloudflare).
4. In a terminal on the i3:
   ```
   cloudflared tunnel login
   cloudflared tunnel create asva
   cloudflared tunnel route dns asva tryasva.com
   cloudflared tunnel route dns asva link.tryasva.com
   ```
5. Create the config file at `C:\Users\<you>\.cloudflared\config.yml`:
   ```yaml
   tunnel: asva
   credentials-file: C:\Users\<you>\.cloudflared\<TUNNEL-ID>.json
   ingress:
     - hostname: tryasva.com
       service: http://localhost:8000
     - hostname: link.tryasva.com
       service: http://localhost:3002
     - service: http_status:404
   ```
   (`<TUNNEL-ID>` is printed by `tunnel create`; the `.json` sits in the same folder.)
6. Double-click **`TUNNEL.bat`**. It runs the tunnel and reconnects if it drops.

Now `https://tryasva.com` shows the landing, and `https://link.tryasva.com/qr`
is the bot's QR page.

No domain yet and just want to test? Run `cloudflared tunnel --url http://localhost:8000`
for a temporary URL that changes each restart.

### A6. Lock the Command Center
The Command Center is protected two ways; use both:
- **Key**: it only opens at `tryasva.com/ops?key=<ADMIN_API_KEY>`. Your key is the
  `ADMIN_API_KEY` line in `C:\ASVA\.env` (also printed when the zip was built).
  Bookmark the full URL with the key.
- **Cloudflare Access** (recommended): in Cloudflare Zero Trust, add an **Access
  application** for `tryasva.com/ops*` that only allows **almmatix@gmail.com**.
  Then even the URL is useless to anyone else.

### A7. Scan the bot WhatsApp
Open `https://link.tryasva.com/qr` (or `http://localhost:3002/qr` on the i3) and
scan it with the phone holding **9344110272**. This links ASVA's own number for
owner messages: digests, alerts, and the assistant.

### A8. Start on boot
So a power cut does not take you offline: press `Win+R`, type `shell:startup`, and
drop shortcuts to **`ASVA_HOST.bat`** and **`TUNNEL.bat`** into that folder. On
every boot the server and tunnel come back by themselves.

---

## Part B - Set up your father's shop laptop

### B1. Create the business (on your laptop)
1. Open `https://tryasva.com/ops?key=...` -> **+ Add business**.
2. Fill shop name (e.g. RISHAB TRADING COMPANY), owner name, the shop's 10-digit
   WhatsApp number (9444294894), plan, and paid months.
3. Click **Create business**. Copy the **agent token** and the **config** shown
   (the token is shown once).

### B2. Install on his laptop
1. On the shop laptop open `https://tryasva.com/download` and download **ASVA for Windows**.
2. Unzip it to **`C:\ASVA`**.
3. Open **`tally_agent\config.json`**, paste the config from B1, and set
   **`company_name`** to his exact Tally company name.

### B3. Run it
1. Double-click **`SETUP.bat`** once (installs dependencies).
2. Double-click **`START.bat`**. From now on this is the daily launcher; it also
   auto-updates ASVA before starting (Part G).
3. Open `http://localhost:3001/qr` and scan with the shop phone (9444294894).

### B4. The Tally "Send to ASVA" button
So a freshly saved bill in Tally reaches ASVA: install the TDL button following
**`TALLY_BUTTON_SETUP.md`** (ships in the shop zip). ASVA exports the bill PDF to
`C:\ASVA\bills`, ASVA picks it up and sends it, then moves it to `bills\sent`.

### B5. Prove it works
- Raise a small test bill in Tally for a customer who has a WhatsApp number -> that customer receives the bill on WhatsApp from the shop number.
- Record a receipt against a bill in Tally -> within a sync the bill flips to paid in ASVA (oldest bills first).

---

## Part C - Add a second (or third) business

Exactly Part B, on that shop's laptop, with its own **+ Add business** entry and
its own WhatsApp scan. Nothing to change on the i3. The bot stays only on the i3;
every shop uses its own number. Do **not** copy one shop's `config.json` to
another - each has its own secret agent token.

---

## Part D - The bot (owner assistant)

- Lives only on the i3, on **9344110272**. Owners message it; it never messages customers.
- Available on **Growth and above**. A Basic-plan owner who messages it is told the assistant is a Growth feature (their bills and reminders keep working).
- Useful commands an owner can send it: `HELP`, `LIST` (open debtors), `CHECK <party>` (a party's balance), `REMIND <party>` (send that party a reminder now), `BILL <party>`, `PAID`.

---

## Part E - Subscriptions and payments

There are two separate "payments". Do not mix them up.

### E1. Customer payments (money the shop's customers owe the shop)
Handled automatically. When your father records a **receipt** in Tally, ASVA reads
it on the next sync and marks that customer's bills paid, oldest first. He never
updates two places. Tally stays the source of truth. Nothing for you to do.

### E2. Subscription (money the shop owes YOU)
Direct UPI, you confirm and click Renew:
1. **Add business** starts a **30-day** cycle.
2. Near expiry the owner gets a WhatsApp renewal notice with the amount and a
   tap-to-pay link to your UPI **9344110272@ybl**.
3. The owner pays you by UPI. You see it land in your own UPI app.
4. In the Command Center (Subscriptions tab), click **+1 mo** on that shop. The
   expiry moves forward and, if they were cut off, **sends resume automatically**
   on the next message (the server recomputes status live).
5. **Grace**: for 3 days after expiry sends still go and the owner is warned. After
   that the account is **suspended** and customer sends stop until you renew.
6. **Suspend** button cuts a shop off immediately (non-payment). **Renew** reverses it.
7. **Change plan**: the plan dropdown on each row moves the tier without touching the expiry.

---

## Part F - Health monitoring

Open the Command Center **Health** tab:
- **System strip**: server, database, bot WhatsApp, email alerts - up or down at a glance.
- **Job chips**: each scheduled job (reminders, digest, subscription check, watchdog) with when it last ran; a stalled job turns red.
- **Per shop, today**: online/offline, that shop's WhatsApp up/down, and sent / failed / blocked / queued counts.
- **14-day traffic**: green sent, red failed.
- **Needs attention**: open alerts, newest first.

It refreshes every 30 seconds. To also get **emailed** the moment something
critical drops, fill these in `C:\ASVA\.env` on the i3 and restart `ASVA_HOST.bat`
(Gmail needs an **app password**, not your normal password):
```
ALERT_EMAIL_TO=almmatix@gmail.com
ALERT_EMAIL_FROM=almmatix@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=almmatix@gmail.com
SMTP_PASS=<gmail app password>
```
The watchdog runs every 5 minutes; it opens an alert once, emails you, and resolves
it when the problem clears, so you are not spammed.

---

## Part G - Shipping an update (auto-updates every shop)

Every shop's `START.bat` runs `updater.py` first: it asks the server if a newer
version exists and, if so, downloads and applies the new app while keeping that
shop's `.env`, `config.json`, and WhatsApp login. So to push an update to
everyone:

1. Make your change and bump `app_version` in `app/config.py`.
2. Build a fresh shop zip: `python build_zip.py shop`.
3. On the i3, replace **`C:\ASVA\downloads\ASVA_shop.zip`** with the new one.
4. Record the release so shops know to update. In Supabase, insert a row into
   `app_releases` with the new `version` (set `mandatory` true only if it must
   not be skipped).
5. Done. Each shop picks it up the next time it opens ASVA. In the Command Center
   the shop's version flips to the new number, and the **Outdated** count drops.

---

## Part H - Manual checks (verify everything, anytime)

A two-minute round to confirm the whole system is healthy:

- **Landing**: open `https://tryasva.com` -> the site loads.
- **API**: open `https://tryasva.com/health` -> `{"status":"ok", ...}`.
- **Command Center**: open `https://tryasva.com/ops?key=...` -> Health tab all green.
- **Bot**: from an owner's phone, message **9344110272** `HELP` -> it replies.
- **Shop send**: father raises a test bill -> the customer gets it on WhatsApp.
- **Payment sync**: record a receipt in Tally -> the bill flips to paid in ASVA.
- **Subscription**: a shop near expiry shows the renewal notice; clicking **+1 mo** moves its date.
- **Update**: after Part G, the shop's version updates in the Command Center within a day.

If any check fails, the Health tab (and your email, once SMTP is set) will point
at which piece is down.
