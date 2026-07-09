# ASVA go-live runbook (2 hours)

Do these in order on your father's laptop. Times are rough. Test everything on
ONE party whose WhatsApp number is YOURS before turning it on for real customers.

Numbers:
- Shop WhatsApp (customers see this): 919444294894  (port 3001)
- Company / agent number (owner commands + OCR): 9344110272  (port 3002)

---

## PHASE A — Setup and connect  (~40 min)

- [ ] 1. Copy the new `ASVA.zip` (from Desktop) to the laptop and unzip it.
- [ ] 2. In TallyPrime: F1 Help > TDL & Add-On > F4 Manage Local TDL. **Remove the
        `ASVA.tdl` line** (it was the crash). Leave `WhatsApp.txt` if you want.
        Restart Tally.
- [ ] 3. Run `SETUP.bat` once. Wait for it to finish (installs Python/Node/Electron).
- [ ] 4. Make sure TallyPrime is open with RISHAB TRADING COMPANY loaded.
- [ ] 5. Launch ASVA: double-click `ASVA.vbs` (or `START.bat`). Window opens with
        the ASVA logo. No black cmd windows.
- [ ] 6. Open the **WhatsApp Setup** tab. Scan:
        - Shop QR from 919444294894
        - Company QR from 9344110272
- [ ] 7. Click the **Status** button (bottom-left). All dots green: backend, shop,
        company, watcher. If a dot is red, wait 30s and re-check.

Checkpoint: app running, both numbers connected, all green.

---

## PHASE B — Verify data and test on ONE party  (~55 min)

Pick a test party and set its number to YOURS (in Tally, or ask me to set it in
the DB). Turn reminders ON for ONLY that party for now.

- [ ] 8. **Data accuracy** — Dashboard: the test party's Baaki + overdue days match
        Tally. Plan meter at top shows active customers (about 183) + plan.
- [ ] 9. **Credit days** — click the party's Credit days button: it shows Tally's
        term + the reminder schedule. Change it, Save, reopen to confirm.
- [ ] 10. **Instant reminder** — from 919444294894 message 9344110272:
         `REMIND <party name>`. Your test number gets a reminder + UPI within seconds.
- [ ] 11. **Owner bot** — send `LIST`, `CHECK <party>`, `HELP` to 9344110272. Replies come.
- [ ] 12. **Customer bot** — from your test number message the shop 919444294894:
         `HISAB`. You get the statement.
- [ ] 13. **Payment detection** — in Tally record a part payment for the test party.
         Within 5 min your number gets "received Rs X, remaining Rs Y".
- [ ] 14. **New bill send** — in Tally set Export folder once to `C:\ASVA\bills`
         (PDF). Make a bill for the test party, Alt+E > Export > Current, name the
         file the bill number. Within 5 min your number gets the exact PDF. The
         file moves to `C:\ASVA\bills\sent`. Make another bill and DON'T export it:
         nothing goes out.
- [ ] 15. **OCR** — from 919444294894 send a photo of a paper bill to 9344110272.
         You get name/amount; reply YES to create it.
- [ ] 16. **Settings** — set send time, language (Hinglish default), weekly-off,
         discount % if you offer one. Use "View message" to preview.

Checkpoint: every step behaved for your test number.

---

## PHASE C — Go live  (~15 min)

- [ ] 17. Reminders are OFF for all parties by default. When ready, Dashboard >
         **Sab ON** (or tick only the parties you want to start with).
- [ ] 18. Safety: sends are paced + capped (25/day) so nothing blasts at once. For
         day one, consider starting with 20-40 known-friendly parties, not all.
- [ ] 19. Set the test party's number back to the real customer's number.
- [ ] 20. Leave the laptop ON with Tally + ASVA running. Reminders go out at your
         set send time; new bills send when you export them.

Done. If any step misbehaves, note the step number + the Status log text and send it.

---

## If something breaks
- Bot not replying: message must be a saved party's number (customer) or the owner
  number to 9344110272 (owner). Unknown numbers only get a reply to HI/HELP.
- WhatsApp keeps disconnecting: close WhatsApp Desktop, end stray chrome.exe, keep
  one ASVA instance.
- Tally wedges after a big pull: wait a few minutes; it recovers.
- Bill didn't send after export: the file name must contain the bill number; check
  `C:\ASVA\bills` (unsent) vs `C:\ASVA\bills\sent` (sent).
