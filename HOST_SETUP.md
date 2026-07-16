# ASVA - Deploy & Operate Guide

Three step-by-step guides (server, website, father's laptop), then the short
sections you come back to (add a business, the bot, subscriptions, health,
updates, checks).

## The map (who runs what)

| Piece | Address | Runs on | Always on? |
|---|---|---|---|
| **Website** (marketing, SEO) | `tryasva.com` | a **free static host** (Cloudflare Pages) | yes, on a CDN |
| **App** (API, dashboard, downloads, agent) | `app.tryasva.com` | the **i3 host** via Cloudflare Tunnel | yes |
| **Bot WhatsApp** (owner assistant) | `link.tryasva.com/qr` | the **i3 host**, port 3002, number **9344110272** | yes |
| **Shop** (Tally + shop WhatsApp) | local, port 3001, number **9444294894** | your **father's laptop** | when the shop is open |

Why the website is separate: hosting it free on a CDN keeps it fast and always
up for Google and AI crawlers, even when the i3 reboots. The app runs on the i3.

Rules that never change:
- A **customer** only hears from the **shop's own number**; the bot number is for the **owner** only.
- The **bot** WhatsApp is scanned on the **i3** (by you); each **shop** WhatsApp is scanned on **that shop's laptop**. That is why it stops disconnecting.
- The **admin key** and Supabase key live only on the **i3 and your browser**, never on a shop laptop.

---

## Before you start: put the domain on Cloudflare (once)

Both the website and the app use Cloudflare, so do this first.

1. Sign up free at **cloudflare.com** -> **Add a site** -> `tryasva.com` -> Free plan. Cloudflare shows you **two nameservers**.
2. In **GoDaddy** (where tryasva.com is registered), open the domain's DNS/nameserver settings and change the nameservers to the two Cloudflare gave you. Remove the old Vercel records for the current landing.
3. Wait for Cloudflare to show the domain as **Active** (a few minutes to a few hours).

(Say "start Cloudflare" any time and I will walk you through these screens live.)

---

## GUIDE 1 - Host the server on the i3

### 1. Install and start
1. Copy **`ASVA_server.zip`** to the i3 and unzip to **`C:\ASVA`**.
2. Double-click **`ASVA_HOST.bat`**. First run installs dependencies (2 to 3 min), then keeps two windows alive: **Backend** (:8000) and **Bot WhatsApp** (:3002).
3. Check on the i3: `http://localhost:8000/health` -> `{"status":"ok", ...}`.

### 2. Never sleep
Right-click **`KEEP_AWAKE.bat`** -> **Run as administrator**. Keep the i3 plugged in.

### 3. Publish the download
Make **`C:\ASVA\downloads`** and copy **`ASVA_shop.zip`** into it. (You replace this file on every update, Guide "Updates".)

### 4. Give the app a public address (Cloudflare Tunnel)
On the i3, install **cloudflared**, then:
```
cloudflared tunnel login
cloudflared tunnel create asva
cloudflared tunnel route dns asva app.tryasva.com
cloudflared tunnel route dns asva link.tryasva.com
```
Create `C:\Users\<you>\.cloudflared\config.yml`:
```yaml
tunnel: asva
credentials-file: C:\Users\<you>\.cloudflared\<TUNNEL-ID>.json
ingress:
  - hostname: app.tryasva.com
    service: http://localhost:8000
  - hostname: link.tryasva.com
    service: http://localhost:3002
  - service: http_status:404
```
Double-click **`TUNNEL.bat`**. Now `https://app.tryasva.com/health` works from anywhere.

### 5. Lock the Command Center
- Open it only at `https://app.tryasva.com/ops?key=<ADMIN_API_KEY>`. Your key is the `ADMIN_API_KEY` line in `C:\ASVA\.env`. Bookmark the full URL.
- Recommended: in Cloudflare **Zero Trust -> Access**, add an application for `app.tryasva.com/ops*` allowing only **almmatix@gmail.com**.

### 6. Scan the bot WhatsApp
Open `https://link.tryasva.com/qr` (or `http://localhost:3002/qr` on the i3) and scan with the phone holding **9344110272**.

### 7. Start on boot
`Win+R` -> `shell:startup` -> drop shortcuts to **`ASVA_HOST.bat`** and **`TUNNEL.bat`** there.

---

## GUIDE 2 - Host the website (free)

The whole marketing site is prebuilt as static files in **`ASVA_website.zip`**
(index + one page each for how-it-works, features, pricing, use-cases, plus
sitemap.xml, robots.txt, llms.txt). Its Download button points at the app on the i3.

**Cloudflare Pages (recommended - the domain is already on Cloudflare, drag and drop, free):**
1. Unzip `ASVA_website.zip` to a folder.
2. In Cloudflare -> **Workers & Pages** -> **Create** -> **Pages** -> **Upload assets**.
3. Name it `asva`, drag the unzipped folder in, **Deploy**.
4. In the new project -> **Custom domains** -> add **`tryasva.com`** (and `www.tryasva.com`). Cloudflare wires the DNS for you.

Now `https://tryasva.com` serves the site from a CDN, always up.

**Alternatives (also free):** Vercel (`npm i -g vercel` then `vercel --prod` in the folder; the `vercel.json` gives clean URLs) or Netlify (drag the folder to app.netlify.com/drop). With any of them, point `tryasva.com` at the host per their custom-domain steps.

**After it is live:** in **Google Search Console**, add `tryasva.com` and submit `https://tryasva.com/sitemap.xml`. That is what gets you indexed and ranked.

To reship the website after a copy change: `python build_zip.py website`, then re-upload the folder (Cloudflare Pages: the same project -> new deployment).

---

## GUIDE 3 - Set up your father's laptop

### 1. Create the business (on your laptop)
Open `https://app.tryasva.com/ops?key=...` -> **+ Add business** -> fill shop name,
owner, the shop's 10-digit WhatsApp (9444294894), plan, months -> **Create business**.
Copy the **Download link**, the **agent token**, and the **config** shown (the
token is shown once). The download is gated, so the shop needs this personal
Download link - a random visitor cannot pull the app.

### 2. Install on his laptop
1. Open the **Download link** you copied -> get **ASVA for Windows**.
2. Unzip to **`C:\ASVA`**.
3. Open **`tally_agent\config.json`**, paste the config from step 1, and set **`company_name`** to his exact Tally company name. (The config already points `backend_url` at `https://app.tryasva.com`.)

### 3. Run it
1. Double-click **`SETUP.bat`** once.
2. Double-click **`START.bat`** (the daily launcher; it also auto-updates ASVA first).
3. Open `http://localhost:3001/qr` and scan with the shop phone (9444294894).

### 4. The Tally "Send to ASVA" button
Install the TDL button per **`TALLY_BUTTON_SETUP.md`** (in the shop zip). ASVA exports the bill PDF to `C:\ASVA\bills`, sends it, then moves it to `bills\sent`.

### 5. Prove it works
- Raise a small test bill for a customer with a WhatsApp number -> they receive it from the shop number.
- Record a receipt in Tally -> the bill flips to paid in ASVA (oldest first).

---

## Add another business

Repeat Guide 3 on that shop's laptop, with its own **+ Add business** entry and its
own WhatsApp scan. Nothing changes on the i3. Never copy one shop's `config.json`
to another - each has its own secret token.

---

## The bot (owner assistant)

Lives only on the i3, on **9344110272**. Available on **Growth and above** (a Basic
owner is told it is a Growth feature; their bills and reminders keep working).
Owner commands: `HELP`, `LIST`, `CHECK <party>`, `REMIND <party>`, `BILL <party>`, `PAID`.

---

## Subscriptions and payments (two different payments)

**Customer payments** (customers -> the shop): automatic. A **receipt** in Tally
makes ASVA mark that customer's bills paid, oldest first. Nothing to do.

**Subscription** (a shop -> you): direct UPI, you confirm and click Renew.
1. **Add business** starts a **30-day** cycle.
2. Near expiry the owner gets a WhatsApp renewal notice with the amount and a tap-to-pay link to your UPI **9344110272@ybl**.
3. They pay you by UPI; you see it in your app.
4. In the Command Center, click **+1 mo** on that shop. Expiry moves forward and, if they were cut off, **sends resume automatically**.
5. **Grace**: 3 days after expiry sends still go (owner warned), then **suspended**.
6. **Suspend** cuts a shop off now; **Renew** reverses it. The plan dropdown moves the tier without touching expiry.

---

## Health monitoring

Command Center **Health** tab: system strip (server, DB, bot WhatsApp, email),
job heartbeats, per-shop sent/failed/blocked/queued, 14-day traffic, open alerts.
Refreshes every 30s. To also be **emailed** when something critical drops, fill in
`C:\ASVA\.env` on the i3 and restart `ASVA_HOST.bat` (Gmail app password, not your
normal password):
```
ALERT_EMAIL_TO=almmatix@gmail.com
ALERT_EMAIL_FROM=almmatix@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=almmatix@gmail.com
SMTP_PASS=<gmail app password>
```
The watchdog runs every 5 minutes and opens each alert once, emails you, and
resolves it when it clears.

---

## Shipping an update (auto-updates every shop)

Each shop's `START.bat` runs `updater.py` first, which pulls a newer version and
applies it while keeping that shop's `.env`, `config.json`, and WhatsApp login. To
push an update to everyone:
1. Make the change and bump `app_version` in `app/config.py`.
2. `python build_zip.py shop`.
3. On the i3, replace **`C:\ASVA\downloads\ASVA_shop.zip`** with the new one.
4. In Supabase, insert a row into `app_releases` with the new `version` (`mandatory` true only if it must not be skipped).
5. Every shop picks it up next launch; the Command Center version flips and the **Outdated** count drops.

---

## Manual checks (any time)

- **Website**: open `https://tryasva.com` -> loads.
- **App**: open `https://app.tryasva.com/health` -> `{"status":"ok", ...}`.
- **Command Center**: `https://app.tryasva.com/ops?key=...` -> Health all green.
- **Bot**: message **9344110272** `HELP` from an owner number -> it replies.
- **Shop send**: father raises a test bill -> the customer gets it.
- **Payment sync**: record a receipt in Tally -> the bill flips to paid.
- **Subscription**: a shop near expiry shows the renewal notice; **+1 mo** moves its date.
- **Update**: after shipping, the shop version updates in the Command Center within a day.
