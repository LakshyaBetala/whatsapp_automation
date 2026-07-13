# ASVA - Manual Test Guide (real hardware)

Everything that CAN be tested automatically already is (66 pytest tests +
live end-to-end runs against the real database). This guide covers the part
only real phones and the two laptops can prove. Run top to bottom once after
deploying new zips; the whole pass takes about 20 minutes.

Terminology: **asva** = the application (shop laptop). **bot** = the ASVA bot
(spare laptop, number 9344110272). **owner** = the smb owner (9444294894).
**party** = a shop debtor.

---

## 1. Shop laptop (asva) comes up

1. Run `START.bat` (or the ASVA desktop app). Three things must start:
   backend (8000), shop WhatsApp (3001), Tally watcher.
2. Open `localhost:8000/health` - expect `"status":"ok"` and
   `"db_reachable":true`.
3. First time only: open `localhost:3001/qr`, scan with the SHOP's WhatsApp.
   Wait for "WhatsApp CONNECTED (Baileys). Ready." - it must NOT loop back
   to a new QR.
4. Close the WhatsApp window and reopen: it must reconnect WITHOUT asking
   for a new scan.

## 2. Bot laptop comes up

1. Run `ASVA_BOT.bat`. Two windows: backend + bot WhatsApp.
2. First time only: scan `localhost:3001/qr` with the BOT phone (9344110272).
3. Same re-open test: restart the window, no new QR.

## 3. Bot commands (from the OWNER phone, chat with 9344110272)

Send these and check each reply:

| Send | Expect |
|---|---|
| `HI` | Simple English menu: exactly 7 commands with `-----` separators |
| `LIST` | "who owes you" list with rupee totals |
| `CHECK <real party>` | That party's bills, phone, reminder ON/OFF |
| `DIGEST` | Today's summary immediately, in plain English |
| `DIGEST 9PM` | "Your daily summary will now come at 9 PM every day." |
| `BILL Test Party 100 45` | "Bill created." with due date 45 days out |
| `MSG Test Party: hello test` | "in the queue. It will go from your shop number." |
| `REMIND Test Party` | queue line (the bot never messages a party itself) |
| From a THIRD phone: `HI` | "only for registered ASVA shop owners", then silence |

## 4. The queue actually delivers from the SHOP number

1. With BOTH laptops running, do the `REMIND Test Party` above.
2. Within ~2 minutes the party phone must receive the reminder
   **from the SHOP number** (not from 9344110272).
3. Now switch the shop laptop OFF and send another `REMIND`.
   Nothing arrives (bot says "queue"). Switch the shop laptop ON -
   the queued message must arrive within ~2 minutes of boot.

## 5. Dashboard round-trip (INSIDE the ASVA desktop app - this matters)

Do this in the DESKTOP APP, not just a browser: the app is Electron, where
pop-up input boxes never worked before. Every edit below now uses an in-page
box (modal), so it must work in the app.

1. The `BILL` you created in step 3 must appear on the dashboard under the
   party, and on the party page with a blue **WhatsApp** tag.
2. Party page -> **Edit party**: change the name and add/fix the WhatsApp
   number, Save - the header updates. (Only non-Tally parties show this; a
   Tally party shows "name and number come from Tally".)
3. Party page -> **+ Add bill** and **Edit** on a non-Tally bill: a box opens
   for amount + bill number. Save - the bill row updates. **Delete** removes it.
4. Party page -> **Delete party**: removes the party and all its bills, then
   returns to the Dashboard. (Non-Tally only.)
5. Dashboard -> non-Tally party -> **Rs Pay**: a box opens for the amount.
   Save - the bill flips to partial/paid and (if WhatsApp is connected) the
   party gets a confirmation.
6. Party page: change credit days - the due date on the open bill must change.
7. Press **Send now** while shop WhatsApp is DISCONNECTED - the dashboard must
   show the real failure reason, not a fake success.
8. Reminders page: add a second batch (English, different hour), save, assign
   the test party to it on the dashboard, reload - assignment sticks.

## 6. Tally flow (shop laptop, Tally open)

1. Make one small sales voucher in Tally. Within the watcher's cycle
   (~5 min) it must appear on the dashboard.
2. If the party has a WhatsApp number: the bill PDF/text arrives from the
   shop number.
3. Enter a receipt in Tally against a Tally party - after sync the party's
   outstanding drops. (Receipts NEVER touch WhatsApp-made bills - those are
   settled with PAID or the dashboard.)

## 7. Automatic schedules (leave both laptops on for a day)

| When | What must happen |
|---|---|
| Each batch's hour (default 11:00) | Reminder sweep sends that batch's due reminders from the SHOP number, paced, capped |
| Your digest hour (default 10 PM; change with `DIGEST 9PM` on the bot) | Daily summary arrives on the owner phone FROM THE BOT number |
| Any time | Customer replies (HISAB / PAID) get answered on the shop number |

## 7a. Command Center + subscriptions (operator only)

The Command Center is YOUR cockpit - it reads the shared database, so it shows
EVERY shop centrally. It is gated by `ADMIN_API_KEY` (set it in the server
`.env`; while empty, the page is off).

1. Open `http://<host>:8000/ops` - a key prompt appears. Enter `ADMIN_API_KEY`.
2. You see KPI cards (businesses, online, active/grace/suspended, messages this
   month, failed today, outdated) and a table with every business: status pill,
   plan, expiry, days left, last seen (green dot = agent seen in the last 5 min),
   agent version, messages used, failed sends today.
3. **Renew +1 mo** on a row - its expiry jumps forward 30 days (stacks on
   remaining days if still active). The row flips to active.
4. **Suspend** on a row - status goes suspended; that shop's WhatsApp sends stop
   immediately (server-side). **Renew** brings it back.
5. Change the **plan** dropdown - the tier changes without touching the expiry.
6. Leave it open - it refreshes every 30 s. Start a shop's ASVA app and within a
   minute its dot goes green and its version appears (via /tally/sync + the
   desktop heartbeat).

Note: a suspended shop still SYNCS Tally (read-only, so its numbers stay
correct), but sends/reminders/digest/OCR stop until renewed.

## 7b. Missed-hour catch-up (laptop off at the send hour)

1. Keep the shop laptop OFF over a batch's send hour (e.g. 11:00), switch
   it on at ~12:30. NOTHING should send by itself.
2. The owner phone gets ONE alert from the bot: "ASVA was not running at
   11:00 ... waiting". The Dashboard shows an amber banner:
   "ASVA was off (11:00) - N parties have reminders waiting" with
   **Send now** and **Skip today** buttons.
3. Press **Send now** - reminders go out from the shop number within
   ~2 minutes. Press **Skip today** instead (on another day) - nothing
   sends today and tomorrow's schedule is normal (no double message).
4. Per-party cancel: Dashboard -> "Who today?" -> press **Skip today**
   next to one party - that party alone is skipped for today.
5. Control test: with the laptop ON at 11:00, reminders must go at 11:00
   automatically WITHOUT any banner or confirmation.

## 8. Recovery drills (do each once, both laptops)

- Kill the backend window -> it must auto-restart within 5 seconds.
- Kill the WhatsApp window -> auto-restarts, reconnects with NO new QR.
- Pull the internet for 2 minutes -> on return, WhatsApp reconnects itself;
  queued sends flow again.
- Reboot the laptop, run the bat again -> no re-setup, no re-scan.

## 9. Multi-company (only if Tally has 2+ companies)

1. On the shop laptop: `python tally_agent\agent.py --companies`
   - lists every company open in Tally, marking which are already connected.
2. `python tally_agent\agent.py --add-company "EXACT COMPANY NAME"`
   - registers it (own separate data + credentials, saved to config.json).
3. `python tally_agent\agent.py --import-masters` - imports ALL connected
   companies' debtors, one company at a time.
4. Restart the ASVA app: a **company dropdown** appears at the top of the
   left menu. Switching it must swap Dashboard/Reminders/Analytics/Accounts
   to that company's own data (the new company starts EMPTY - correct).
5. Company names on screen come FROM TALLY: rename the company in Tally,
   sync, reload - the dashboard heading follows.
6. Data accuracy check per company: pick 2 parties in each company and match
   their outstanding against Tally to the rupee.

## 10. Cleanup after testing

On the bot, send `PAID Test Party` to settle the test bill, or delete the
party from the dashboard flow you prefer. Done.

---

## If something fails

- Bot replies always tell you WHY a send failed (service down / QR pending /
  number not on WhatsApp / plan limit). Fix that cause and resend.
- Phone says "could not link device" while scanning? Do this once:
  1) On the phone: Linked Devices -> the old ASVA entry -> Log out.
  2) Press **Re-link (fresh QR)** (in WhatsApp Setup, or on localhost:3001/qr).
  3) Scan the fresh QR. No file deleting needed - Re-link does the cleanup.
- The service now self-heals: a dead connection after laptop sleep / wifi
  change reconnects by itself within ~2 minutes, with NO re-scan. If two
  ASVA WhatsApp windows run with the same session it logs a loud CONFLICT
  line - close the extra window.
- Both laptops must stay awake (Power Options: never sleep) and both linked
  PHONES must stay online (WhatsApp logs out linked devices after 14 days
  of the phone being offline).
