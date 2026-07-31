# ASVA mobile app — design spec (build later)

Status: **approved as design-only** (2026-07). Not built yet. This is the plan we
agreed so we can build it after the pilot is stable.

## The one principle that decides everything

**The engine stays on the shop laptop. The phone is a companion, not the engine.**

Reading Tally and sending on the shop's own WhatsApp both require the shop laptop
(Tally runs there; WhatsApp is linked to that machine). A phone can do neither. So
the mobile app is the owner's **remote control + glance screen** over the same
ASVA backend — never a replacement for the desktop.

## What the app IS

A read-heavy companion that talks to the same backend (`app.tryasva.com`) the
desktop already uses, scoped to one business.

### Include (safe on a phone)
- **Today**: total outstanding, who to chase today, sent/paid/failed counts.
- **Per party**: dues, next reminder, and the Promise-to-Pay card (paused-until +
  the customer's own words) — the same data the desktop party page shows.
- **Promises**: everyone who promised to pay and by when; who broke a promise.
- **Push notifications**: a customer promised to pay, a customer paid, the shop's
  WhatsApp went down, the laptop went offline.
- **Light actions**: mark a party paid / exclude (do-not-chase) / hold or resume a
  reminder / trigger one manual reminder.

### Restrict (desktop-only — never on the phone)
- Tally connection / company selection (needs to sit next to Tally).
- WhatsApp QR linking / re-linking (the session lives on the laptop).
- Plan / billing changes and anything that spends money.
- Bulk destructive actions.

The restriction is enforced **server-side** (a mobile session's token is not
allowed to hit setup/billing endpoints), not just hidden in the UI.

## How it authenticates

Reuse the pairing pattern we already trust: the desktop shows a short **login
code** (or a QR); the phone enters it once and receives a **mobile session token**
scoped to that business, read-mostly. No Supabase key, no agent token on the
phone. Revocable from the desktop.

## How to build it (recommended path)

1. **Phase 1 — PWA** served by the backend at `app.tryasva.com/m`:
   - Reuses the existing API and rendering; installs to the home screen; supports
     web push. Ships in days, not weeks. No app store.
   - This is the right first version — it proves the scope with almost no new
     surface area.
2. **Phase 2 — native (optional, later)**: React Native or Flutter against the
   same API, only if we want an app-store listing and richer native push. Same
   scope; weeks of work + a separate toolchain.

## New backend surface needed (small)

- `POST /mobile/login` — redeem a desktop login code → mobile session token.
- `GET /mobile/summary` — today's numbers + who-to-chase (reuses build_ops/admin data).
- `GET /mobile/party/{id}` — the party view incl. the promise card.
- `GET /mobile/promises` — open promises.
- `POST /mobile/action` — the whitelisted light actions only (mark paid, exclude,
  hold/resume, one manual reminder). Everything else is refused for mobile tokens.
- Web-push registration + the existing alert events wired to push.

## Open questions to settle before building

- Multi-company owners: switch business in the app, or one token per business?
- Do we let the owner send a *free-form* WhatsApp to a party from the phone, or
  only the pre-approved reminder? (Leaning: pre-approved only, at first.)
- Offline behaviour: the phone can show cached data, but actions need the backend
  reachable — queue actions, or require online?

## Why not now

The pilot's priority is the desktop engine being rock-solid at real shops. The
phone app adds value once shops are live and the owner wants to watch from
outside the shop. Building it now would split focus for a companion feature.
