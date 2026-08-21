# ASVA backend — production hardening & security review

Date: 2026-08-21. Scope: the FastAPI backend now running on the Kamatera VPS
(`app.tryasva.com`), the `wa_service` inbound path, the thin-client outbox, and
the Command Center. Trigger: a shop showed "33 messages stuck in queue" plus a
request to make the cloud backend production-ready.

Severity key: **P0** exploit/data-loss now · **P1** fix before scaling shops ·
**P2** hardening / hygiene.

---

## 1. The "33 messages stuck in queue" incident (RESOLVED in code; backlog clear pending)

**What happened.** RISHAB's `wa_outbox` had 33 `queued` rows: one at the head
retried **722 times** over ~3 days (`503` = the shop WhatsApp session was down),
and 32 newer reminders behind it with **0 attempts**. The shop laptop was off
around the 2.0.1 update, so nothing drained and nothing expired.

**Root causes.**
1. The shop drainer (`tally_agent/agent.py` `_drain_once`) `break`s the whole
   batch on any transient failure. Correct when the WhatsApp *session* is down
   (don't hammer), but it meant the oldest row was re-attempted every cycle while
   the rest waited.
2. **Age-expiry only ran inside `outbox.pull()`** — i.e. only while a shop laptop
   is online. With the shop off for days, the backlog just grew, and on reconnect
   it would try to deliver 1–3 day-old reminders (bad for customers, a ban
   signal).

**Fixes (this commit).**
- `_drain_once` now only stops the batch when the session is down *and nothing
  has sent yet*; a permanent per-message failure (`success:false`, e.g. not on
  WhatsApp) marks that row failed and **continues**, so one bad recipient can't
  starve newer reminders.
- New `app.jobs.outbox_sweep.expire_stale()` runs **on the queuing host (VPS)
  every 15 min, independent of any shop being online**, capping the queue at
  `EXPIRE_HOURS` (72h). A shop that was dark for a week now comes back to a clean
  queue instead of a stale blast. Registered in `app/scheduler.py`.

**Still pending (needs operator go-ahead):** clear RISHAB's existing 33 stale
rows (they are 1–3 days old; fresh reminders re-queue on the next sweep). This is
a production-data write, correctly gated.

---

## 2. Security findings

### P0 — Unauthenticated inbound webhook → owner impersonation & data exfiltration  ✅ FIXED (deploy pending)
`POST /webhooks/aisensy` was public and trusted the client-supplied `channel`
field. On the live VPS `AISENSY_WEBHOOK_SECRET` is empty and `wa_service` sent no
secret, so **anyone on the internet** could:
```
POST https://app.tryasva.com/webhooks/aisensy
{"data":{"sender":"<a shop's WhatsApp number>","message":"LIST","channel":"bot"}}
```
and have the **owner command handler** run for that shop — the reply (full debtor
list, balances) is returned in the HTTP response — or run `PAID`/`STOP`/`BILL`
to corrupt data or sabotage reminders. Shop numbers are public (on invoices), so
this was directly exploitable.

**Fix (code):** `app/routers/webhooks.py` now authenticates every inbound POST:
- `channel="bot"` (owner commands): requires the shared `AISENSY_WEBHOOK_SECRET`
  (our ASVA assistant number sends it; a stranger cannot). Until the secret is
  configured on a server it is allowed-with-a-loud-warning, so setting the secret
  is what closes it — **that is the deploy step below.**
- `channel="shop"` (silent customer capture): if an `x-agent-token` is present it
  must map to a real paired shop; wa_service now sends the shop's own token.
- A supplied-but-wrong secret is rejected on any channel.
`wa_service/index.js` sends `x-webhook-secret` (bot) / `x-agent-token` (shop).
Regression tests added: `test_webhooks.py::test_bot_channel_*`.

**Deploy to fully close it:** set `AISENSY_WEBHOOK_SECRET` on the VPS backend and
the same value as `WEBHOOK_SECRET` on the bot `wa_service`; deploy the new backend
+ wa_service; restart both. Ship 2.0.2 so shop laptops send `x-agent-token`.

### P1 — No rate limiting anywhere  ✅ FIXED (basic)
Token/key brute force, webhook spam, and cheap DoS were all unthrottled.
**Fix:** an in-process per-IP sliding window (`app/main.py` middleware) on the two
public endpoints (`/webhooks/aisensy` 120/min, `/webhooks/allowlist` 30/min),
keyed by `CF-Connecting-IP`. Generous enough that real traffic never trips.
*Next:* front the whole domain with Cloudflare WAF + rate rules (P2).

### P1 — Public API docs / schema  ✅ FIXED
`/docs`, `/redoc`, `/openapi.json` were public, disclosing the full endpoint
surface. **Fix:** disabled when `APP_ENV=production` (`app/main.py`).

### P1 — Secrets in URL query strings  ⚠️ PARTIALLY MITIGATED
`/admin?token=…`, `/ops?key=…`, `/webhooks/allowlist?token=…` put credentials in
URLs (logged by Cloudflare/uvicorn, leak via `Referer`). **Mitigation now:**
`Referrer-Policy: no-referrer` on every response so they don't leak onward.
**Proper fix (P2):** move the admin/ops credential to a header or short-lived
signed cookie set once at load; keep the token out of the querystring.

### P1 — jinja2 3.1.5 (CVE-2025-27516)  ✅ FIXED
Bumped to `jinja2==3.1.6`. **Next:** add `pip-audit` to CI (P2) — pin & scan the
rest (fastapi/starlette/httpx/supabase).

### P2 — No security headers  ✅ FIXED
Added `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`X-Frame-Options: SAMEORIGIN`, and HSTS in production (`app/main.py`).

### P2 — Non-constant-time token compare (tally)  ✅ FIXED
`_verify_token` used `!=`; now `secrets.compare_digest`. (Ops already used it.)

### Confirmed GOOD (no change needed)
- RLS is deny-all for the anon key (migration 032); the service-role key is
  server-side only; tenant isolation is enforced by `business_id` filtering and
  `_verify_token` on every `/tally/*` call.
- Webhook always returns 200 (no BSP retry storm) and dedups on message id.
- Central error capture (`errorlog`) + health watchdog + email alerts exist.
- `dev/app-config.json` holds only mock values (`dev-agent-token`). No secrets
  are committed (`.env`, `remote.env`, `config.json` are git-ignored).

---

## 3. Deploy plan (operator go-ahead required — production changes)

1. **Clear RISHAB's 33 stale queued rows** (psycopg2 UPDATE → `cancelled`).
2. **Set secrets:** generate a random value; put `AISENSY_WEBHOOK_SECRET=<v>` in
   the VPS backend `.env` and `WEBHOOK_SECRET=<v>` in the bot `wa_service`
   environment.
3. **Deploy** the new `app/` + `wa_service/` to the VPS; `systemctl restart
   asva-backend asva-bot` (as root — `server-asva` can't sudo).
4. **Verify:** owner sends a bot command (e.g. `DIGEST`) and gets a reply
   (proves the bot sends the secret); an anonymous `channel:bot` POST returns
   `{"ok":true}` and does nothing.
5. **Ship 2.0.2 exe** so shop laptops send `x-agent-token` (also carries the
   outbox drain fix); then tighten the shop channel to require the token.

## 4. Remaining roadmap (not yet done)
- **P2** Cloudflare WAF + platform rate rules + Bot Fight Mode on `app.tryasva.com`.
- **P2** Move admin/ops credential out of the querystring (header/signed cookie).
- **P2** `pip-audit` in CI; pin & scan all deps; Dependabot.
- **P2** Backups/PITR drill for Supabase (see `RESTORE.md`); verify restore.
- **P2** Rotate any credentials ever pasted in chat (DB password, Resend key,
  the `admin_api_key` that appeared in build logs).
- **P2** Structured request logging that redacts `token`/`key`/`secret`.
- **P3** Consider per-shop webhook secrets instead of one fleet secret once >100
  shops; consider Meta official API as the opt-in premium transport.
