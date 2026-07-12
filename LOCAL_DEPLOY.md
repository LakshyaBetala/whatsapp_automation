# Self-hosting on the spare laptop (i3 HP)

The spare laptop (the **host**) runs **four processes**: the FastAPI backend
(port 8000) and **two** WhatsApp Node services:

- **3001 = shop number** — sends bills/reminders, receives customer replies (HISAB/PAID)
- **3002 = ASVA bot number** — owner-only assistant (LIST, BILL, photo bills, EOD digest)

Both WhatsApp accounts link *from the host* (WhatsApp Web is location-independent),
so bills, reminders and the digest fire even when the shop laptop is off (e.g.
Sundays). The Tally agent (`Asva.exe`) runs on the **Tally PC** and posts to the
host. The database stays on Supabase (cloud).

```
Tally PC ──HTTP:8000──> host laptop ┬─ FastAPI backend ──> Supabase (cloud)
                                    ├─ wa_service :3001 (shop)  ──> WhatsApp
                                    └─ wa_service :3002 (bot)   ──> WhatsApp
```

**If the host is NOT on the shop's LAN** (at home/office), the Tally PC reaches
it over a private tunnel. Install **Tailscale** on both machines (same account);
each gets a stable `100.x.y.z` address. The agent's `backend_url` then points at
the host's Tailscale IP (see "On the Tally PC" below). No router port-forwarding,
no public exposure.

## One-time setup on the i3 laptop

### 1. Install prerequisites
- **Python 3.12+** — check "Add to PATH" during install
- **Node.js 20+** (needed for `wa_service` — it drives a headless Chromium)
- **GTK3 runtime** (for invoice PDFs): install
  [GTK3 Runtime for Windows](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases)
  — without it the app still runs, but bills go out without a PDF attachment.
- **Git**, then: `git clone https://github.com/LakshyaBetala/whatsapp_automation.git`

### 2. Python deps + env
```powershell
cd whatsapp_automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```
Edit `.env` and fill in:
- `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` (Supabase → Settings → API)
- `PUBLIC_BASE_URL=http://localhost:8000`
- `OPENWA_URL=http://localhost:3001` (shop number — customer-facing sends)
- `PLATFORM_WA_URL=http://localhost:3002` (bot number — owner digest/alerts)
- Leave the AiSensy keys empty — sends go through wa_service, not AiSensy.

### 3. Node deps
```powershell
cd wa_service
npm install
```

### 4. Open the firewall for the Tally PC (run PowerShell **as admin**)
```powershell
netsh advfirewall firewall add rule name="TallySaaS Backend 8000" dir=in action=allow protocol=TCP localport=8000
```
Port 3001 stays closed — only the backend on this same laptop talks to it.

### 5. Keep the laptop awake (run as admin)
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```
Keep it plugged in. Closing the lid: Control Panel → Power Options →
"When I close the lid" → **Do nothing** (plugged in).

### 6. Find this laptop's LAN IP
```powershell
ipconfig
```
Note the IPv4 address (e.g. `192.168.1.50`). Give the laptop a **static IP /
DHCP reservation** in the router so it never changes.

## Running

Double-click **`START.bat`** — opens four windows (backend + shop WhatsApp
:3001 + bot WhatsApp :3002 + Tally watcher). Each auto-restarts if it crashes.

**First run only — scan two QRs** (WhatsApp → Linked devices):
- **`http://localhost:3001/qr`** → scan with the **shop's** WhatsApp number
- **`http://localhost:3002/qr`** → scan with the **ASVA bot** WhatsApp number
  (a separate account you control — this is the 24/7 owner helpline)

Each session persists in `wa_service/.baileys_auth/session-<SESSION_ID>/`
(`SESSION_ID` = `default` for shop, `bot` for the bot) — no re-scan on restarts.

Verify:
- `http://localhost:8000/health` → `{"status":"ok", "supabase_configured":true, ...}`
- `http://localhost:3001/api/wa/status` → `{"ready":true, ...}` (shop)
- `http://localhost:3002/api/wa/status` → `{"ready":true, ...}` (bot)
- From the Tally PC's browser: `http://<host-ip>:8000/health` must load too
  (LAN IP, or the host's Tailscale `100.x.y.z` if remote).

### Auto-start on boot (Task Scheduler)
Task Scheduler → Create Task:
- Trigger: **At log on** (and enable auto-login on the laptop, or use "At startup")
- Action: Start a program → `C:\...\whatsapp_automation\START.bat`
- Settings: check "If the task fails, restart every 1 minute"

## On the Tally PC

1. Copy the `Asva` folder (exe + `config.json`).
2. Edit `config.json`:
   ```json
   "backend_url": "http://192.168.1.50:8000"
   ```
   Use the host's **LAN IP** if both are on the shop network, or the host's
   **Tailscale IP** (`http://100.x.y.z:8000`) if the host is remote. The agent
   refuses to run while the placeholder is set.
3. In TallyPrime: F1 → Settings → Connectivity → act as server, port 9000.
4. First run: `Asva.exe --import-masters` (pushes all debtors).
5. Daily (or via Task Scheduler at e.g. 8pm): `Asva.exe --sync`.

## Staying connected + not getting banned (important)

**Docker helps with CRASHES, not BANS.** Running the WhatsApp service in Docker
(see `docker-compose.yml`, used for the bot laptop) isolates Chromium so it never
hits the Windows `EBUSY` lockfile crash or clashes with another Chrome. It does
**nothing** for WhatsApp bans — bans are behavioural. ASVA already ships the
behaviours that keep the number safe:

- **Warm-up cap** (`DAILY_REMINDER_CAP`, default 25/day) so a fresh backlog does
  not blast hundreds of messages on day one. Raise it slowly (25 → 50 → 100 → 0).
- **Human-like pacing**: a random **12-40s gap** between every send
  (`send_gap_min_s`/`send_gap_max_s`), in the sweep *and* in bulk `REMIND`.
- **Number validation**: `wa_service` checks a number is actually on WhatsApp
  before sending (`getNumberId`) and skips it otherwise — sending to non-WhatsApp
  numbers is a top ban signal. Skips log as `failed`.
- **Only known contacts**: bills/reminders go to your own customers; the bot
  answers only registered owners; customer opt-out (STOP / "band karo") is
  honoured silently.
- **Keep the linked phone online** — WhatsApp unlinks a device idle ~14 days.

**Shop laptop: keep the desktop app / `START.bat` auto-restart** (do NOT dockerize
the shop). The Electron supervisor already restarts a crashed `wa_service`, clears
the lockfile and brings back a QR. Docker there would fight the supervisor and add
a Docker-Desktop dependency for the shopkeeper. Docker is worth it only on the
**bot laptop** (yours), which runs `wa_service` standalone.

## Operational notes

- **Never run the backend with more than 1 uvicorn worker** — the EOD digest
  and reminder scheduler run inside the web process; 2 workers = every
  customer gets every message twice. `start_backend.bat` is already correct.
- Message sends are audited in the Supabase `messages` table
  (`delivery_status`: sent / failed / limit_reached).
- If WhatsApp disconnects (phone offline for weeks), `/api/wa/status` shows
  `ready:false` and sends log as `failed` — re-scan the QR.
- Logs: both service windows; agent writes `agent.log` next to the exe.
- Moving to Railway later: everything is env-driven (`OPENWA_URL`,
  `PUBLIC_BASE_URL`), `railway.json`/`Procfile` are already in the repo —
  only wa_service must stay on a machine with a persistent browser session.
