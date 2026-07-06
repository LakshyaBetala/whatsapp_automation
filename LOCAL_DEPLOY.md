# Self-hosting on the spare laptop (i3 HP)

The spare laptop runs **two processes**: the FastAPI backend (port 8000) and
the WhatsApp Node service (port 3001). The Tally agent (`Asva.exe`)
runs on the **Tally PC** and posts to this laptop over the LAN. The database
stays on Supabase (cloud) — the laptop needs internet, but customers/agents
only talk to the laptop.

```
Tally PC ──HTTP:8000──> i3 laptop ┬─ FastAPI backend ──> Supabase (cloud)
                                  └─ wa_service (Node) ──> WhatsApp
```

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
- `OPENWA_URL=http://localhost:3001` (default — both on this laptop)
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

Double-click **`START.bat`** — opens two windows (WhatsApp service +
backend).

**First run only:** open **`http://localhost:3001/qr`** in a browser and scan
the QR with the **business WhatsApp number** (WhatsApp → Linked devices).
The session persists in `wa_service/.wwebjs_auth/` — no re-scan on restarts.

Verify:
- `http://localhost:8000/health` → `{"status":"ok", "supabase_configured":true, ...}`
- `http://localhost:3001/api/wa/status` → `{"ready":true, ...}`
- From the Tally PC's browser: `http://<laptop-ip>:8000/health` must load too.

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
   (the i3 laptop's IP — the agent refuses to run while the placeholder is set)
3. In TallyPrime: F1 → Settings → Connectivity → act as server, port 9000.
4. First run: `Asva.exe --import-masters` (pushes all debtors).
5. Daily (or via Task Scheduler at e.g. 8pm): `Asva.exe --sync`.

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
