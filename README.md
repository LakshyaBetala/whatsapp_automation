# WhatsApp Tally SaaS

WhatsApp-native business automation for Indian wholesale SMBs.

> Connect your Tally. Your customers get automatic bills and reminders on WhatsApp. You get your money faster. **₹599/month.**

This repo contains two deployable pieces:

| Piece | Where it runs | What it does |
|-------|---------------|--------------|
| **`app/`** — FastAPI backend | Railway (cloud) | Scheduler (EOD digest, reminders), WhatsApp send/receive, PDF bills, DB |
| **`tally_agent/`** — Windows agent | Owner's Tally PC | Polls TallyPrime `localhost:9000`, pushes vouchers/receipts/outstanding to the backend |

## Phase 1 status (what's scaffolded here)

- [x] Supabase schema — 6 tables (`migrations/001_initial_schema.sql`)
- [x] FastAPI app skeleton + config + Supabase client
- [x] APScheduler wired in-process (EOD 9pm, reminder sweep, keep-alive ping)
- [x] EOD 9pm digest builder (pure SQL, no AI)
- [x] Reminder schedule engine (Day 7/15/30/45/60, credit-aware, blackout-aware)
- [x] WhatsApp service (AiSensy) — send wrapper + inbound webhook (LIST / STOP / PAID)
- [x] Tally agent skeleton — XML request builders, poller, system-tray stub
- [x] WeasyPrint invoice template + PDF service
- [x] Outstanding one-click import service

> Anything needing live credentials (AiSensy API, Supabase keys, a running Tally) is marked `# TODO` and isolated behind a service boundary so the rest runs without it.

## Quick start (backend)

```powershell
cd C:\Users\laksh\whatsapp-tally-saas
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in Supabase + AiSensy keys
uvicorn app.main:app --reload
```

Open http://localhost:8000/health and http://localhost:8000/docs

## Apply the database migration

The migration is plain Postgres SQL. Apply it via the Supabase SQL editor, the
Supabase MCP `apply_migration`, or `psql`:

```powershell
psql "$env:SUPABASE_DB_URL" -f migrations\001_initial_schema.sql
```

## Tally agent (owner's Windows PC)

```powershell
cd tally_agent
pip install -r requirements.txt
# In TallyPrime: F12 -> Configure -> Data Synchronization -> Set as Server (Port 9000)
python agent.py            # run live
# build the distributable:
pyinstaller --onefile --noconsole --name TallyAgent agent.py
```

## Project layout

```
whatsapp-tally-saas/
├─ app/                  FastAPI backend (deploys to Railway)
│  ├─ main.py            app entry + lifespan + scheduler start
│  ├─ config.py          settings from env (pydantic-settings)
│  ├─ db.py              supabase-py client singleton
│  ├─ models.py          enums + pydantic schemas
│  ├─ scheduler.py       APScheduler jobs registry
│  ├─ routers/           health, tally ingest, aisensy webhook
│  ├─ services/          whatsapp, digest, reminders, payments, pdf, outstanding, templates
│  └─ jobs/              eod_digest, reminder_sweep, keepalive
├─ tally_agent/          Windows .exe agent (PyInstaller)
├─ migrations/           Supabase SQL
├─ templates/            invoice HTML + WhatsApp message text
├─ requirements.txt
├─ Procfile              Railway start command
└─ .env.example
```

## Cost floor (month 0, no customers): ₹420/mo (Railway Hobby).
First ₹599 customer is already profit. Break-even on fixed costs = 4 customers.
