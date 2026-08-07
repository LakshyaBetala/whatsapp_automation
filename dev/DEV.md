# ASVA dev module - run the whole app on this laptop, no Tally

This lets you build and test ASVA for the 4 days away with **no Tally installed**
and **zero risk to the 4 live shops**. It runs a fully isolated local Supabase
(in Docker) and feeds it sample data through the REAL /tally endpoints.

## Prerequisites (already installed on this laptop)
- Docker Desktop (must be **running** - start it from the Start menu if the DB
  commands say "daemon not running")
- Node (for `npx supabase`) and the Python venv (`.venv`)

## One-time / after a reboot
```bash
bash dev/run_dev.sh db-up        # start the local Supabase stack (Docker)
```
The local DB survives across restarts; you only re-run this after stopping it or
rebooting. The database URL/keys are already wired into `dev/.env.dev`
(these are Supabase's public local demo keys - not secrets).

## Daily use
```bash
bash dev/run_dev.sh up           # starts the DB (if down) + backend on :8000 (auto-reload)
# in another terminal:
bash dev/run_dev.sh seed         # load a dozen sample debtors + bills + receipts
```
Then open:
- Owner dashboard : http://localhost:8000/admin?token=dev-agent-token
- Payments tab    : http://localhost:8000/admin/payments?token=dev-agent-token
- Command Center  : http://localhost:8000/ops?key=devkey
- DB browser (Studio): http://127.0.0.1:54323

The backend auto-reloads on every code edit, so you develop exactly like prod.
WhatsApp sends are a harmless no-op in dev (pointed at a dead port), and the
scheduler jobs (reminders/digest/monitor) are off, so the dev app stays quiet.

## Reset the data
```bash
bash dev/run_dev.sh seed         # re-seeds (reuses the same dev business)
# or wipe the whole dev DB and re-apply all migrations:
bash dev/run_dev.sh reset
```

## Prove the payment pipeline (what we fixed in 1.9.4)
```bash
set -a; source dev/.env.dev; set +a
.venv/Scripts/python dev/test_selfheal.py     # create -> confirm -> claim -> self-heal -> posted
```

## Pitch / video demo - the REAL app on mock data (no code, no Tally)

This runs the actual desktop app (the exact .exe UI: dashboard, per-party pages,
credit days, next reminder, Send Now, Payments, analytics, WhatsApp Setup, tour,
help) against the local mock data. Pairing + Tally are bypassed via a dev-only
config, so nothing real is touched.

1. Start Docker Desktop.
2. `dev\RUN_DEV.bat`   - starts the dev DB + backend (keep this window open).
3. `dev\SEED_DEV.bat`  - loads mock customers, bills, payments, and 2 receipts
   already sitting in the Payments tab.
4. `dev\RUN_DEV_APP.bat` - opens the REAL ASVA app on the mock data. It skips the
   setup code + Tally (dev-only `ASVA_DEV_CONFIG` -> dev\app-config.json) and goes
   straight to the working dashboard. Your real ASVA pairing is never touched.

Show payment DETECTION live during the pitch (editable number + amount):
- `dev\SIMULATE_PAY.bat` - prompts for a customer number + amount, then that
  customer "reports a payment" through the SAME webhook a real WhatsApp reply
  hits. Watch it appear in the app's Payments tab in real time.
  (Seeded customer numbers: 919812300001 ... 919812300012. Two of them,
  Venkatesh Hardware and Deepak Electric House, have NO number - use them to
  demo adding/editing a customer's WhatsApp number in the app.)

WhatsApp: the app's "WhatsApp Setup" tab shows the real QR (its own wa_service on
:3001). Scan with any spare number to demo a live connection; sends then work.
Payment detection works whether or not a number is linked.

## Notes
- If migrations change, apply new ones to the dev DB:
  `bash dev/run_dev.sh reset` (or `docker exec -i supabase_db_dev psql -U postgres -d postgres < migrations/0NN_*.sql`).
- After the RLS-lockdown migration, the local `service_role` needs grants that
  Supabase cloud gives automatically. If the app gets "permission denied",
  re-run: `docker exec -i supabase_db_dev psql -U postgres -d postgres < dev/_dev_grants.sql`.
- Stop the DB (keeps data): `bash dev/run_dev.sh db-down`.
