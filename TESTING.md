# ASVA pilot test sequence

Run these in order on your father's laptop. Each step says what to do, what to
expect, and how long it takes. Do the whole thing against ONE test party first.

Pick a test party whose WhatsApp number is one YOU control (safest: in Tally,
temporarily set one party's number to your own so you receive the messages).
In the Dashboard, turn reminders ON for only that party.

IMPORTANT before go-live: reminders are currently OFF for all existing parties
(so nothing sends by accident). When you are ready to go live for real, open the
Dashboard and click "Sab ON" (or tick only the parties you want). New parties
imported from Tally after this update default to ON. The daily cap + spacing
mean even "Sab ON" drips out safely, it never blasts everyone at once.

Plan meter: the top of the Dashboard shows "<active debtors> / <cap> active
customers" and ASVA's recommended plan. Active debtors = parties with an open
bill AND a WhatsApp number (not your full ledger). Your shop shows ~183, so ASVA
recommends the Basic plan.

Credit period: reminders auto-scale to each party's credit terms from Tally. A
30-day party is nudged at ~3/7/15/21/30 days, a 90-day party at ~9/21/45/63/90,
a 6-month party spread across ~180 days, a 1-year party across ~365. Override any
party with the bot command: TERMS <party> <days> (e.g. TERMS Ramesh 180).

Numbers used below:
- Shop WhatsApp (customers see this): 919444294894
- Company/agent number (OCR + owner commands): 9344110272

---

## 0. One-time setup (about 10 min)

1. Unzip `ASVA.zip`. Run `SETUP.bat` once (installs Python deps, Node, Electron).
2. Create the TDL folder: ASVA makes `C:\ASVA\bills` automatically on start, but
   you can also make it by hand.
3. IMPORTANT: do NOT load ASVA's own TDL (it has been removed — it was causing a
   Tally crash). If you still have `ASVA.tdl` in TallyPrime's Manage Local TDL,
   remove that line and restart Tally. Use your EXISTING whatsapp TDL for PDFs.
   To send the exact Tally PDF, set `bill_pdf_dir` in config.json to the folder
   your whatsapp TDL exports bills to. Leave it blank to send text + UPI only.
4. Launch ASVA (double-click `ASVA.vbs`, or `START.bat`). The window opens with
   the ASVA logo in the top-left.

Expected: the app opens, no black cmd windows, logo visible.

---

## 1. Both WhatsApp numbers connected (about 3 min)

Open the **WhatsApp Setup** tab. You see two QR panels (Shop and Company).
- Scan Shop QR from 919444294894.
- Scan Company QR from 9344110272.

Expected: each panel turns to "Connected". Click the **Status** button
(bottom-left): all dots should be green (backend, shop, company, watcher).
Once scanned, it should not ask again on restart.

---

## 2. Dashboard shows accurate data (about 2 min)

Open the **Dashboard** tab. Find your test party.

Expected: its Baaki (outstanding) and Days overdue match TallyPrime exactly.
If it is blank, click **Reload data** and wait one sync cycle (up to 5 min).

---

## 3. Credit days + reminder schedule (per party)

In the Dashboard party list, each party has a **Credit days** button (shows the
value fetched from Tally, or "set" if none). Click it.

Expected: a popup shows the party's credit days and the exact days ASVA will send
reminders (e.g. 30 days -> 3, 7, 15, 21, 30; 90 days -> 9, 21, 45, 63, 90).
Change the number and Save. The schedule updates, and the party's bills now use
the new terms. You cannot edit the interval itself, only the credit days. That is
by design: ASVA sets the timing, you set the terms.

---

## 4. New bill: export from Tally (you choose which bills go out) — no TDL

ASVA picks up bill PDFs from `bill_pdf_dir` (default `C:\ASVA\bills`, created
automatically). Use Tally's built-in Export — no TDL needed.

Setup (once): in TallyPrime do one Export and set the folder to `C:\ASVA\bills`
and format to PDF. Tally remembers it.

Per bill you want to send:
1. Open the invoice in Tally, press **Alt+E > Export > Current**.
2. Set the file name to the **bill number** (e.g. `2526RTC0203`), format PDF, then
   Export.
3. Within one sync cycle (up to 5 min) the test party receives that EXACT Tally
   PDF + message + UPI link; the Status log shows `... new bill(s) sent`.

Expected extras:
- After it sends, the PDF moves itself into `C:\ASVA\bills\sent\` (folder stays
  clean, nothing is ever re-sent).
- Make another bill and DO NOT export it — confirm nothing goes out. Only the
  bills you export are sent. That is the control.

Note: the exported filename must contain the bill number, so ASVA sends the right
bill to the right party. If a PDF's name has no bill number, ASVA safely skips it
(better to skip than send the wrong customer's bill).

---

## 5. Instant reminder (about 1 min)

From 919444294894, message 9344110272:  `REMIND <party name>`

Expected: within seconds the test number gets a reminder with the UPI link, in
your chosen language. (Scheduled reminders go out automatically at the Send time
set in the Reminders tab; this command is just the instant test.)

---

## 6. Owner bot commands (about 2 min)

From 919444294894 to 9344110272, one at a time:
- `LIST`   > list of parties with outstanding
- `CHECK <party name>`   > that party's balance and overdue
- `HELP`   > how-to guide
- `TEAM ASVA not sending`   > escalates to the product team
- `list` (lowercase)   > works the same (commands are case-insensitive)

Expected: a clear reply to each.

---

## 7. Payment detection (partial payment) (about 5 min)

In Tally, record a part payment for the test party (for example, receive 1000
against the open bill).

Expected: within one sync cycle the test number gets
"received Rs 1,000, remaining Rs X." The Status log shows `payments_detected`.
Record another part payment and confirm it fires once, not repeatedly.

---

## 8. Customer self-service (about 1 min)

From the test number (the "customer"), message the shop number 919444294894:
- `HISAB`   > their statement / outstanding
- `PAID`    > owner (919444294894) gets a nudge to record it in Tally

Expected: statement returns; owner gets the nudge.

---

## 9. Photo-bill OCR on the company number (about 2 min)

From 919444294894, send a clear PHOTO of a paper bill to 9344110272.

Expected: ASVA replies with the detected party name and amount. Reply `YES` to
create the bill; `NO` to discard.

---

## 10. Settings take effect (about 2 min)

Open the **Reminders** tab (in the Dashboard/settings card):
- Switch language to English, Save. Send `REMIND <party>` again > message is in
  English. Switch back to Hinglish if you prefer.
- Set a weekly-off day (for example Sunday) and add a holiday date, Save.
- Add a custom line, Save > it appears at the end of the next reminder.

Expected: each change shows up in the next reminder.

---

## 11. Accounts tab (about 1 min)

Open the **Accounts** tab. Change the UPI ID or bank details, Save.

Expected: the next bill/reminder UPI link uses the new value.

---

## 12. New: message preview, discount, send-now, plan usage, sub-tabs

Open the **Dashboard** tab (the reminder settings page).

12a. **Plan usage** — at the top you see "Pro plan (Rs1999/month) — <used> / 5000 messages this month" with a bar. Send a reminder, reload, and the used count goes up.

12b. **View message** — in the settings card, pick a language and style, set discount to 2, then click **View message**. A popup shows the exact reminder, including the "2% off" line and the discounted amount. Change language to English and click again; the popup is in English. (This does not send anything.)

12c. **Early-pay discount** — set discount to 2, Save settings. Now use **Send now** on a test party (or `REMIND <party>`). The customer's QR and the shown amount are 2% lower, and the message has the discount line. Set it back to 0 when done.

12d. **Send now** — in the party table, click **Send now** on your test party, confirm the prompt. The customer gets the reminder immediately. The button shows "Sent".

12e. **Tally vs Non-Tally** — above the party list, toggle **Tally / Non-Tally / All**. Non-Tally shows only parties created from photo/OCR bills (test after you run the OCR step 9); Tally shows synced parties.

12f. **Non-Tally payment** — after creating an OCR bill (step 9), open the Non-Tally sub-tab. That party has a **₹ Pay** button. Click it, enter an amount (say part of the bill). The bill goes to partial/paid, the party gets "received Rs X, remaining Rs Y", and the outstanding on the dashboard drops. Tally parties do not have this button (they settle from Tally automatically).

Note on sending safety: scheduled reminders now go out with a randomised gap between each message and a daily cap, so ASVA never blasts everyone at once (that is what gets a WhatsApp number banned). "Send now" for a single party is always immediate.

---

## What to send me if something fails

For any step, tell me the step number, what you saw, and the exact text from the
Status log or the Tally popup. For step 3 also send the PDF filename in
`C:\ASVA\bills`. I will fix and rebuild.
