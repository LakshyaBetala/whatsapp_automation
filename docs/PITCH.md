# ASVA one-page pitch + 5-minute demo script

Copy rules: plain language, no em/en dashes, English page is pure English and the
Hinglish page is pure Hinglish. Fill the [PROOF] line with a real number from the
4 live shops before you use this.

---

## ONE-PAGER (English)

**ASVA gets your udhaari paid, on WhatsApp, automatically.**

The problem: every distributor has lakhs stuck in the market. Chasing customers
is awkward, takes hours, and always slips. You already have all the data in
Tally, but Tally does not chase anyone for you.

What ASVA does:
- Reads your Tally (sales, receipts, outstanding) with nothing to type twice.
- Sends bills and polite payment reminders on your OWN WhatsApp number, on a
  schedule you control.
- Every morning it tells you exactly who to chase today and how much.
- When a customer says they paid or sends a screenshot, ASVA catches it and puts
  it in one tap ready to post into Tally.

Why it is different:
- It runs itself. You are not doing the chasing, ASVA is.
- Customers hear from your number, not some company bot. No app for them.
- It uses your Tally as the source of truth. ASVA never touches your money and
  takes no cut of any payment.

The offer:
- Free pilot till 15 September 2026. No card, no commitment.
- After that a flat yearly price. No per-message charges, no percentage of
  collections, ever.

Proof: [PROOF: e.g. "In one month ASVA helped a shop bring back Rs 3.4 lakh."]

Next step: 15 minutes to set it up on your PC next to Tally, then it works on its
own. Reply YES and we will get you started this week.

---

## ONE-PAGER (Hinglish)

**ASVA aapki udhaari WhatsApp par, khud-ba-khud wapas dilata hai.**

Problem: har distributor ke lakhs market mein atke hote hain. Customer ko baar
baar bolna awkward hai, time lagta hai, aur reh jaata hai. Saara data Tally mein
hai, par Tally aapke liye kisi ko chase nahi karta.

ASVA kya karta hai:
- Aapki Tally padhta hai (sales, receipts, outstanding), dobara kuch type nahi.
- Bill aur payment reminder aapke APNE WhatsApp number se bhejta hai, aapke chune
  hue time par.
- Roz subah batata hai aaj kis kis ko chase karna hai aur kitna.
- Customer "paid" bole ya screenshot bheje, to ASVA use pakad kar ek tap mein
  Tally mein daalne ke liye ready kar deta hai.

Alag kyun hai:
- Khud chalta hai. Chasing aap nahi, ASVA karta hai.
- Customer ko aapke number se message jaata hai, kisi company bot se nahi. Unke
  liye koi app nahi.
- Source of truth aapki Tally hai. ASVA paise ko haath nahi lagata, na koi cut
  leta hai.

Offer:
- 15 September 2026 tak free pilot. Na card, na commitment.
- Uske baad ek flat saalana price. Na per-message charge, na collection ka
  percentage, kabhi nahi.

Proof: [PROOF: jaise "Ek mahine mein ASVA ne ek shop ke Rs 3.4 lakh wapas dilaye."]

Aage: 15 minute mein Tally ke saath aapke PC par set, phir khud chalta hai.
YES bhejein, is hafte shuru karte hain.

---

## 5-MINUTE LIVE DEMO SCRIPT

Setup before you walk in: on your laptop run RUN_DEV.bat, SEED_DEV.bat, then
RUN_DEV_APP.bat (the real app on mock data, see dev/DEV.md). Keep SIMULATE_PAY.bat
ready.

- 0:00 Hook (30s). "You have money stuck in the market. Let me show you how ASVA
  brings it back without you chasing anyone." Open the app on the dashboard.
- 0:30 Who to chase (60s). Point at the dashboard: outstanding per party, days
  overdue, credit days, and the next reminder ASVA will send. "Every morning it
  tells you exactly this, ranked by who matters most."
- 1:30 One customer (60s). Open a party page: their bills, balance, promise to
  pay, reliability score. Show Send Now. "One tap, from your own number."
- 2:30 Payment detection, live (60s). Run SIMULATE_PAY.bat, enter a number and an
  amount. Switch to the Payments tab, it appears instantly. "When a customer says
  they paid, ASVA catches it and gets it ready for Tally. One tap and it is
  posted. Nothing typed twice."
- 3:30 Your number, your control (45s). Open WhatsApp Setup (the QR). "It sends
  from your own number, so customers trust it." Show Reminders: batches, timing,
  skip a day, language. "You are always in control."
- 4:15 Close (45s). "It reads Tally, chases on WhatsApp, and shows you who to
  chase, on its own. Free pilot till September, flat price after, we never touch
  your money. Can I set it up on your PC this week?"

Handling the two common questions:
- "Is my data safe?" Your data stays in your Tally and our secure server. ASVA
  only reads it to send reminders. It never moves money.
- "Will it spam my customers?" No. You set the schedule, it sends a few polite
  messages from your own number, and any customer can reply STOP.
```
