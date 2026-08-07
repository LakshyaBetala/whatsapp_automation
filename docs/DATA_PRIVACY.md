# ASVA Data and Privacy - the one-pager for the audit-fearful shop

Problem #3: shops fear that if a GST officer audits them and sees ASVA, it means
trouble. This sheet is what you hand them (and read aloud). Copy rules: plain
language, no em/en dashes, English page pure English, Hinglish page pure Hinglish.

---

## ENGLISH

**ASVA does not add any audit risk. It is a reminder tool, nothing more.**

- **ASVA only reads. It never writes your books.** It reads your existing Tally
  data (who owes what) so it can send reminders. It does not create a second set
  of accounts, does not change your Tally, and does not invent any record.
- **ASVA never touches money.** No payments pass through ASVA. It takes no cut,
  holds no funds, and issues no receipts of its own. When a customer pays, YOU
  record it in your Tally, as always.
- **Your data stays yours.** It lives in your Tally on your PC and on ASVA's
  private, secured server. It is never sold and never shared with anyone.
- **Cleaner records, not riskier ones.** Regular reminders and clear outstanding
  tracking make your books tidier, which helps at audit time, not hurts.
- **You are in control.** You choose what goes out and when. You can pause it,
  or ask us to delete your data, any time.

One line to remember: "ASVA reminds. It does not keep books, move money, or share
your data."

---

## HINGLISH

**ASVA se audit ka koi extra risk nahi. Ye sirf ek reminder tool hai, aur kuch nahi.**

- **ASVA sirf padhta hai, aapki books mein kuch likhta nahi.** Aapki maujooda
  Tally data padhta hai (kaun kitna deta hai) taaki reminder bhej sake. Na doosra
  hisaab banata hai, na Tally badalta hai, na koi naya record banata hai.
- **ASVA paise ko haath nahi lagata.** Koi payment ASVA se hokar nahi jaata. Na
  cut leta hai, na paisa rokta hai, na apni koi receipt banata hai. Customer paise
  de to aap hamesha ki tarah apni Tally mein daalte hain.
- **Aapka data aapka hi rehta hai.** Wo aapke PC ki Tally mein aur ASVA ke private,
  secure server par rehta hai. Na kabhi becha jaata hai, na kisi ke saath share.
- **Records saaf hote hain, risky nahi.** Regular reminder aur clear outstanding
  tracking se aapki books zyada saaf rehti hain, jo audit mein madad karta hai.
- **Control aapka hai.** Kya jaayega aur kab, aap decide karte hain. Kabhi bhi
  pause kar sakte hain, ya apna data delete karwa sakte hain.

Yaad rakhne ki ek line: "ASVA yaad dilata hai. Na hisaab rakhta hai, na paisa
chalata hai, na data share karta hai."

---

## Engineering backing (make the claims TRUE - checklist)
- [ ] PII (party numbers, balances) encrypted at rest on the server.
- [ ] Service key server-side only, never on a shop laptop or browser (already so).
- [ ] A real "delete my data" path (per-business wipe) an owner can request.
- [ ] No third-party data sharing; no analytics that export PII.
- [ ] Phase 2: self-host the DB on our own server (see [deploy/VPS_MIGRATION.md]
      Mode B) so the pitch line "your data never leaves our private server" is
      literally true.
