# ASVA Bot - setup on the spare/old laptop

The bot is the owner's 24/7 WhatsApp assistant (LIST / CHECK / REMIND / BILL /
photo bills / the 10 PM digest). It runs on its **own laptop** with its **own
WhatsApp number**, completely separate from the shop. The two laptops never talk
to each other - they share the same Supabase cloud database.

**No Docker needed.** This runs natively and heals itself.

## What the laptop needs

Just **Windows + internet**. `ASVA_BOT.bat` installs everything else itself:

- If **Python** (3.11-3.13) is missing, it installs Python 3.12 via winget.
  (It never uses 3.14 - that has no pydantic wheel yet.)
- If **Node.js** is missing, it installs Node LTS via winget.
- The WhatsApp engine is **Baileys** - it talks to WhatsApp directly with **no
  browser at all**. No Chrome, no Edge, no Chromium download. This is what fixed
  the old "connects then LOGOUT" loop.

No Tally, no shop WhatsApp, no Docker, no browser. (If winget is blocked on the
machine, install Python 3.12 from python.org and Node LTS from nodejs.org, rerun.)

## Setup + run - ONE file

1. Unzip `ASVA_bot.zip` anywhere (e.g. `C:\ASVA_bot`).
2. Double-click **`ASVA_BOT.bat`**.

On the first run it will, by itself:
- copy `.env.bot` to `.env`,
- build a Python `.venv` on a supported Python and install packages (~2 min),
- install the WhatsApp service Node packages (~3 min),
- start the backend + the bot, and open the QR page.

3. When the QR page opens (`http://localhost:3001/qr`), scan it with the
   **bot's WhatsApp number** (WhatsApp -> Linked Devices -> Link a Device).
4. Wait for the window to say **"WhatsApp CONNECTED (Baileys). Ready."**.

Every day after that: just double-click `ASVA_BOT.bat`. It skips setup and starts.

## Test it

The bot answers only the **registered owner number**, and that number must be
**different** from the bot's own number (WhatsApp can't message itself). So from
the **owner's phone**, open a chat with the **bot number** and send `HI`, then
`LIST`. You should get a reply within a few seconds.

## Scan ONCE - that is the design

You do **not** need to re-scan weekly. The QR scan links this laptop as a
WhatsApp "linked device" and the login is saved on disk
(`wa_service\.baileys_auth`). It survives restarts, crashes, internet drops
and reboots with **no re-scan**. There are only three things that ever force
a new QR, and all three are avoidable:

1. **The bot PHONE stays offline for 14+ days.** WhatsApp then logs out all
   linked devices (their security rule). Fix: keep the bot phone charged, on
   the internet, and open WhatsApp on it once in a while. Do this and the scan
   lasts indefinitely.
2. **Someone unlinks the device** from the phone (WhatsApp -> Linked devices).
   Don't tap "Log out" there.
3. **The `.baileys_auth` folder is deleted** on the laptop. Don't delete it
   (except when you WANT a clean re-link).

**WhatsApp Web version updates need NO re-scan.** On every start/reconnect the
service reads the current version straight from web.whatsapp.com and uses it
automatically. The version is just a handshake number - your login is separate
and stays valid across version upgrades.

## Who messages whom (important)

- **Parties (debtors) only ever hear from the SHOP's own number.** When you
  use REMIND / MSG / BILL on the bot, the message is queued in the cloud and
  the shop laptop sends it from the shop number within a minute or two.
  The bot replies "queue ho gaya, shop number se jayega" so you know.
- **You (the owner) hear from the BOT number**: command replies, the 9 PM
  digest, alerts.
- If the shop laptop is off, queued messages simply WAIT and go out when it
  comes back online (stale ones expire after 48 hours instead of surprising
  a party days later).

## Robustness (built in)

- Both windows **auto-restart** if the service ever crashes.
- No browser, so no Chromium/EBUSY/"Loading 100% -> LOGOUT" problems.
- A **transient disconnect reconnects on its own with NO re-scan** (Baileys
  resumes the session), with growing backoff so WhatsApp is never hammered.
- A genuinely dead session (logged out / corrupt) is wiped automatically and a
  fresh QR appears - no manual folder surgery needed.
- Sends are **paced** (12-40s apart) and numbers are **checked on WhatsApp** before
  sending, to protect the number from bans.
- Keep the laptop **awake** (Power Options -> never sleep, do nothing on lid close)
  and keep the bot phone's WhatsApp online.

## If you ever want a 100% clean re-link

Close the two windows, delete the folder `wa_service\.baileys_auth`, and run
`ASVA_BOT.bat` again to scan a fresh QR.
