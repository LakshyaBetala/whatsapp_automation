# Step 2a: Shop WhatsApp on the Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shop's WhatsApp can run on ASVA's server, so reminders and bills go out (and replies are read) while the shop laptop is off, with no double sends from old desktops and no desktop release needed.

**Architecture:** Reuse the field-hardened single-session `wa_service/index.js` unchanged: one Node process per server shop, each on its own localhost port, started and supervised by a small manager (`wa_service/manager.js`, one systemd unit, runs as `server-asva`, no root). The manager asks the backend which shops to run. The backend gains a `wa_transport` switch per shop: `server` shops are drained by a new server job that reuses `outbox.pull`/`ack` exactly as a laptop would; the laptop endpoint hands nothing to a `server` shop. The owner connects from a server-rendered page (`/admin/whatsapp`) that shows the QR.

**Tech Stack:** Python/FastAPI, Node 20 (Baileys, express), PostgreSQL (Supabase), systemd.

Spec: `docs/superpowers/specs/2026-10-20-asva-reliability-and-smart-replies-design.md`, Step 2 (2.1-2.3). Deviations, decided for robustness and simplicity:
- One process per shop (process isolation, proven code) instead of one multi-session process. About 85 MB per shop: fine to ~30 active shops on 4 GB; revisit at 25.
- Session files are protected by the Google-encrypted disk, 0700 permissions and the AES-encrypted state bundle; app-level encryption is deferred to before the Kamatera move.
- Desktop 2.2.0 (thin client, 5 tabs) is step 2b; nothing here needs it.

---

## File map

| File | Change |
|---|---|
| `migrations/056_wa_transport.sql` | `wa_transport`, `wa_port`, `wa_server_requested_at`, `wa_linked_at` on businesses |
| `app/services/wa_server.py` | new: port allocation, child URL, status/QR fetch, connect/transport logic |
| `app/routers/internal.py` | new: `GET /internal/wa-shops` for the manager (localhost + key) |
| `app/routers/admin.py` | `/admin/whatsapp` page + `/admin/whatsapp/start` + `/admin/whatsapp/status` |
| `app/services/outbox.py` | `pull(..., caller="laptop")`: nothing for `server` shops |
| `app/jobs/server_outbox.py` | new: drains `server` shops through their child, hourly cap, refreshes `wa_ready` |
| `app/scheduler.py` | register `server_outbox` every minute |
| `app/services/monitoring.py` | `wa_down` also for `server` shops (laptop state irrelevant) |
| `app/main.py` | include the internal router |
| `wa_service/manager.js` | new: spawn/supervise one `index.js` per server shop |
| `deploy/linux/bootstrap.sh` | `asva-shops.service` |
| tests | `test_wa_server.py`, `test_server_outbox.py`, extend `test_outbox.py`, `test_monitoring.py` |

## Tasks

1. **Migration 056** (`wa_transport text not null default 'laptop' check in ('laptop','server')`, `wa_port int unique`, `wa_server_requested_at timestamptz`, `wa_linked_at timestamptz`).
2. **`wa_server.py`** (pure helpers, tested): `allocate_port(used: set[int], base) -> int`; `child_url(port)`; `async status(port) -> dict|None` (GET `/api/wa/status` with `x-asva-key`); `phase(status) -> 'starting'|'qr'|'connected'`; `request_server(db, biz)` (sets requested_at, assigns port); `mark_connected(db, biz_id)` (sets `wa_transport='server'`, `wa_linked_at` if null, `wa_ready=true`).
3. **Internal endpoint** `GET /internal/wa-shops`: only from 127.0.0.1/::1 and only with `x-asva-key == settings.wa_api_key`; returns `[{business_id, port, token}]` for shops with `wa_server_requested_at` set and not suspended.
4. **Laptop guard**: `outbox.pull(db, biz, limit, caller="laptop")` returns `[]` when the shop's `wa_transport == 'server'`; the server job calls it with `caller="server"`.
5. **Server job** `server_outbox.run()`: for each `server` shop with a port: refresh `wa_ready`/`wa_checked_at` from the child's status; if ready, pull (`caller="server"`), cap at 30 sent in the last hour, send each with the existing random gap to `http://127.0.0.1:{port}/api/wa/send`, ack `sent`/`failed`/keep `queued` on transient (503/connect) exactly like the laptop. Registered every minute.
6. **Owner page** `/admin/whatsapp`: explains in plain words ("Reminders keep going when this computer is off. Your phone stays the same; ASVA appears in Linked devices and you can remove it any time"), a Start button, then the live QR (polls status every 3 s), then "Connected". `status` marks the shop connected the first time the child reports ready.
7. **Monitoring**: for `server` shops, `wa_down` is raised whenever `wa_ready` is false, whatever the laptop state.
8. **Manager** `manager.js`: every 60 s fetch `/internal/wa-shops`; spawn `node index.js` per shop with env `PORT`, `SESSION_ID=shop-<id>`, `WA_CHANNEL=shop`, `WA_TOKEN=<agent token>`, `WA_API_KEY`, `WA_BIND_HOST=127.0.0.1`, `BACKEND_URL`, `WA_AUTH_DIR=<wa_service dir>` (so the existing state bundle carries the sessions); restart crashed children with backoff (2 s doubling to 60 s); stop children whose shop left the list; prefix child logs with the shop id.
9. **Bootstrap + live install**: `asva-shops.service` (User `server-asva`, `EnvironmentFile=/etc/asva-bot.env`, `BACKEND_URL=http://localhost:8000`, `Restart=always`); sudoers restart rule extended to it; install on the live box.
10. **Ship**: full tests, migration, deploy, enable the unit, smoke test (manager starts a child for a test request, QR appears on `/admin/whatsapp`), then the founder links Rishab's number.

Done when: a shop moved to `server` keeps sending with its laptop off; its old desktop receives nothing from `/license/outbox/pull`; tests pass; the manager survives a child crash.
