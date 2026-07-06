# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WhatsApp-native billing/reminder automation for Indian wholesale SMBs. A shop's TallyPrime data (sales, receipts, outstanding debtors) is pushed to a cloud backend, which sends customers WhatsApp bills + payment reminders and gives the owner an end-of-day digest.

The system is **two cooperating processes that talk over HTTP**:

1. **`app/`** — FastAPI backend, deploys to Railway. Owns the database, the scheduler, WhatsApp send/receive, and PDF generation.
2. **`tally_agent/`** — a Windows CLI (shipped as a PyInstaller `.exe`) that runs on the shop owner's PC next to Tally. It POSTs Tally data up to the backend's `/tally/*` endpoints. It never touches the database directly.

These two only ever communicate through the `/tally/import` and `/tally/sync` HTTP endpoints — keep that contract in sync when you change either side.

## Commands

Backend (from repo root):
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then fill in Supabase keys
uvicorn app.main:app --reload    # http://localhost:8000/docs
```

Tests (pytest, in repo root):
```powershell
pytest                              # all
pytest test_tally_routers.py        # one file
pytest test_tally_xml.py -k parse   # one test by keyword
```
Tests that touch `app.main`/`app.routers.bills` transitively import WeasyPrint. See the WeasyPrint gotcha below if import fails.

Tally agent (owner's PC — talks to TallyPrime on `localhost:9000`):
```powershell
cd tally_agent
python agent.py --import-masters    # one-time: push all debtors -> /tally/import
python agent.py --sync              # daily: push day book -> /tally/sync
# build the shipped exe:
pyinstaller --onefile --name Asva agent.py
```
The shipped build + its `config.json` live in `Asva/` (git-ignored).

Database migrations are **not run by the app**. Apply `migrations/*.sql` in order via the Supabase SQL editor, the Supabase MCP `apply_migration`, or `psql "$env:SUPABASE_DB_URL" -f migrations\NNN_*.sql`.

Deploy: push to `main`; Railway builds via `railway.json` (Nixpacks) / `Procfile` and health-checks `/health`.

## Architecture notes that aren't obvious from one file

**Degraded-mode boot.** The app is designed to start with *no* credentials. `db.get_client()` returns `None` (not raise) when Supabase isn't configured; endpoints that truly need the DB call `require_db()`, which raises with a clear message. `app/main.py`'s lifespan logs warnings for missing Supabase/AiSensy but boots anyway. Don't add module-import-time code that requires live credentials.

**Service-role key = no RLS.** The backend uses the Supabase *service-role* key (`SUPABASE_SERVICE_KEY`) and bypasses Row Level Security entirely. This key must stay server-side; it must never reach the agent or a browser. All tenant isolation is done in application code by filtering on `business_id` — there is no DB-level guard, so every query must scope by `business_id`.

**Router/service split.** Routers (`app/routers/`) validate input and call a service; business logic lives in `app/services/`. Keep routers thin.

**In-process scheduler.** `app/scheduler.py` runs APScheduler *inside* the web process (started in the lifespan), so it only runs while a web dyno is up. Three cron jobs: EOD digest (21:00), reminder sweep (10:00), and a 6-hourly Supabase keep-alive ping (free-tier projects pause after ~7 days idle). All in `settings.timezone` (Asia/Kolkata).

**Plan limits are enforced atomically in Postgres.** `whatsapp.send_message` calls the `increment_usage_if_allowed` RPC (defined in `migrations/002_atomic_usage.sql`) which does a `SELECT ... FOR UPDATE` check-and-increment. Every send is also written to the `messages` table for audit — including blocked sends (`delivery_status="limit_reached"`). Plan tiers and their numeric limits live in `PLAN_LIMITS` in `app/models.py`.

**Enums are duplicated by hand.** The `Enum` values in `app/models.py` mirror Postgres enum types in `migrations/001_initial_schema.sql`. Changing one means changing both.

**Tally `/sync` does FIFO payment allocation.** In `app/routers/tally.py`, a `Receipt` voucher is applied against that client's oldest open bills first, splitting across bills and marking each `paid`/`partial`. Sales vouchers are deduped by `voucher_number` (upsert). A new sales bill for a client that has a `whatsapp_number` triggers `_generate_and_deliver` (PDF + WhatsApp) as a FastAPI background task. Opening balances come in via `/tally/import` as synthetic `OB-<name>` bills with `is_opening_balance=True`.

**Inbound webhook must always return 200.** `app/routers/webhooks.py` handles both the Meta GET verification handshake (`WEBHOOK_VERIFY_TOKEN`) and inbound POSTs. The POST path dedups on message ID via the `messages` table and *never* returns non-200 (the BSP retries on non-200, causing double-processing). Inbound commands (`LIST`, `STOP <name>`, `PAID`, …) are routed to `app/services/bot.py`.

## Known divergences / gotchas (check before trusting)

- **WhatsApp transport: OpenWA, not AiSensy.** Naming in config, `.env.example`, templates, and DB columns still says **AiSensy**, but sends actually go through the Node microservice in `wa_service/` (`POST {OPENWA_URL}/api/wa/send`, default `http://localhost:3001`, configurable via `OPENWA_URL`). The `aisensy_message_id` DB column stores the OpenWA message id. `wa_service` must be running and QR-authenticated (`http://localhost:3001/qr` for first-time linking) for real sends; failures are logged to `messages` with `delivery_status="failed"`. **Inbound** flows the same way: `wa_service` forwards received messages to the backend's `/webhooks/aisensy` (shape `{data:{sender,message,messageId}}`) and sends the bot's reply back on the same chat — so bot commands work without any Meta webhook.
- **Deployment target is a local Windows laptop, not Railway** (for now). See `LOCAL_DEPLOY.md` for the runbook; `SETUP.bat` (once) and `START.bat` (daily) launch everything. The backend MUST run with exactly 1 uvicorn worker — the APScheduler jobs run in-process and extra workers duplicate every send. `railway.json`/`Procfile` remain for a later cloud move.
- **Agent ↔ backend contract lives in `app/routers/tally.py`.** The backend's `TallyImportPayload`/`TallySyncPayload` (token in the *body*, `debtors`/`vouchers` field names) is the source of truth; `tally_agent/agent.py` builds payloads to match. If you change one side, change the other and re-run `pytest test_tally_routers.py`. The shipped `Asva/Asva.exe` must be rebuilt (PyInstaller) after any `tally_agent/` change.
- **Tally integration is TDL-free** (inline-TDL collection queries only; the `*.tdl` files in the repo root are dead). Hard-won facts encoded in `tally_agent/tally_xml.py`, all verified against a live TallyPrime with 3 companies (a 930-debtor electricals trader and a 1,966-debtor ₹28Cr chemicals firm): `SVCURRENTCOMPANY` is mandatory with >1 company open; ledger balances are **negative when the customer owes** (parse flips the sign); **`OpeningBalance` (FY start) — not `ClosingBalance` — seeds the OB bill**, because sync replays the FY's vouchers on top of it (closing would double-count); voucher **collections and Day Book ignore `SVFROMDATE`/`SVTODATE` over HTTP**, but the **`Voucher Register` report honours them** (the `TYPE="Date"` attribute matters) — sync fetches it month-by-month because a full-FY export of a big shop times Tally out, and Tally's single-threaded HTTP server can wedge for minutes after an aborted big request; Voucher Register leaves the top-level `AMOUNT` empty — the total is the party's own `LEDGERENTRIES.LIST` entry; the backend dedups replays (sales via `bills.tally_voucher_number`, receipts via `tally_receipts` from migration 005); responses arrive as UTF-16-BOM *or* UTF-8 depending on query type (`sanitize_xml` is BOM-aware — don't "simplify" it); debtors live in nested subgroups under Sundry Debtors (154 street/route groups in one shop, 2 in another), so the group tree must be walked; per-ledger `BillCreditPeriod` carries real terms (1–90 days observed, plus junk like a date string — `parse_credit_days` tolerates it).
- **WeasyPrint needs native libs.** WeasyPrint `dlopen`s GTK/Pango/Cairo at import; `app/services/pdf.py` imports it lazily inside `generate_invoice_pdf` so the app boots on a bare Windows box. Railway's Nixpacks buildpack ships the libs, so PDF generation only works in production (or after installing the GTK3 runtime locally).
- **Column names bite.** `bills` has `tally_voucher_number` (unique with `business_id`) and `invoice_number` — there is no `voucher_number`. `clients` has `credit_days` (not `default_credit_days`) and `name` is NOT NULL.

## Product name

The product is **ASVA**. The FastAPI app title, the agent exe (`Asva/Asva.exe`),
and the bat scripts all use it. The old pilot layer (dashboard/, server.js,
run_pilot.py, TDL files) was deleted in July 2026 — `wa_service/` is NOT part
of that; it is the live WhatsApp transport. Photo-bill OCR uses Gemini
(`GEMINI_API_KEY`, free tier) via `app/services/ocr.py`.
