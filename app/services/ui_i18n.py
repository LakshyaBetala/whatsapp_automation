"""Trilingual UI layer for the ASVA admin + phone SCREENS (the app chrome).

Separate from app.services.i18n, which localises the WhatsApp BOT's owner
replies (english / hinglish only). This module localises the on-screen UI and
supports three real audiences:
  * english  - younger owners / operators comfortable in English
  * hinglish - Roman-script Hindi ("Party dhundo"), fast and casual
  * hindi    - Devanagari Hindi ("पार्टी ढूंढें"), for owners who read the
               script fluently and find Roman-Hindi harder than English

Two kinds of screen need translating, and the OLD one-directional
find/replace table could only ever reach the first:

1. SERVER-rendered admin pages (dashboard, party, reminders, ...) are
   AUTHORED in Hinglish. `translate_html(html, lang)` rewrites that Hinglish
   source into English or Devanagari (Hinglish is the no-op source).

2. CLIENT-rendered screens (Today, the phone PWA) build their text in
   JavaScript, so no server-side pass can touch them - that is why Today and
   /m used to stay English whatever the toggle said. For those we ship a small
   JSON dictionary + a `TT()` helper into the page; the JS looks every visible
   string up by its English key. `js_runtime(lang)` emits that.

Both directions read from the SAME catalog below, so a string is defined once.
"""
from __future__ import annotations

import json

LOCALES = ("english", "hinglish", "hindi")
LABELS = {"english": "English", "hinglish": "Hinglish", "hindi": "हिंदी"}
# Short label shown ON the toggle for each language, in its own script.
NATIVE = {"english": "English", "hinglish": "Hinglish", "hindi": "हिंदी"}


def norm(lang: str | None) -> str:
    """Any input -> one of LOCALES. 'hi'/'hindi'/Devanagari map to hindi;
    'hinglish' stays hinglish; anything else (blank, unknown) -> english."""
    s = (lang or "").strip().lower()
    if s == "hinglish":
        return "hinglish"
    if s in ("hindi", "hi") or s.startswith("hin"):
        return "hindi"
    return "english"


def is_en(lang: str | None) -> bool:
    return norm(lang) == "english"


def next_lang(lang: str) -> str:
    """Cycle english -> hinglish -> hindi -> english for a single toggle."""
    order = list(LOCALES)
    try:
        return order[(order.index(norm(lang)) + 1) % len(order)]
    except ValueError:
        return "english"


# ── SERVER-page catalog: (hinglish source, english, devanagari hindi) ─────────
# The admin pages are written in the first column. Keep the source strings byte
# -identical to what the templates emit or the replace will miss them.
UI: list[tuple[str, str, str]] = [
    # ---- Dashboard ----
    ("Tick = us party ko reminder ON. Batch, timing aur holidays: <b>Reminders</b> tab.",
     "Tick = reminders ON for that party. Batches, timing and holidays: <b>Reminders</b> tab.",
     "टिक = उस पार्टी के लिए रिमाइंडर चालू। बैच, समय और छुट्टियाँ: <b>Reminders</b> टैब।"),
    ("messages is month", "messages this month", "इस महीने के मैसेज"),
    ("active customers is month", "active customers this month", "इस महीने के सक्रिय ग्राहक"),
    ("Naya data aaya - dekhein", "New data arrived - view", "नया डेटा आया - देखें"),
    ("Tally bills", "Tally bills", "टैली बिल"),
    ("Non-Tally bills", "Non-Tally bills", "बिना-टैली बिल"),
    ("Party dhundo...", "Search party...", "पार्टी ढूंढें..."),
    ("Baaki: zyada pehle", "Dues: highest first", "बकाया: ज़्यादा पहले"),
    ("Overdue: zyada din pehle", "Overdue: most days first", "ओवरड्यू: ज़्यादा दिन पहले"),
    ("Naam: A to Z (baaki wale)", "Name: A to Z (owing only)", "नाम: A से Z (बकाया वाले)"),
    ("Naam: A to Z", "Name: A to Z", "नाम: A से Z"),
    ("Sabko reminder ON karo", "Turn reminders ON for all", "सबका रिमाइंडर चालू करें"),
    ("Sabko reminder OFF karo", "Turn reminders OFF for all", "सबका रिमाइंडर बंद करें"),
    (">Sab ON<", ">All ON<", ">सब चालू<"),
    (">Sab OFF<", ">All OFF<", ">सब बंद<"),
    ("Agla reminder", "Next reminder", "अगला रिमाइंडर"),
    ("Aaj kaun?", "Who today?", "आज कौन?"),
    ("number nahi", "no number", "नंबर नहीं"),
    # ---- Missed-hour catch-up banner + per-party skip ----
    ("ASVA band tha (", "ASVA was off (", "ASVA बंद था ("),
    (" parties</b> ke reminder ruke hain (total ", " parties</b> have reminders waiting (total ",
     " पार्टियों</b> के रिमाइंडर रुके हैं (कुल "),
    ("Abhi bhejein", "Send now", "अभी भेजें"),
    ("Aaj skip karein", "Skip today", "आज छोड़ें"),
    (">Aaj skip<", ">Skip today<", ">आज छोड़ें<"),
    ("Bheje ja rahe hain... thodi der me status dekhein.", "Sending now... check back in a minute.",
     "भेजे जा रहे हैं... थोड़ी देर में स्टेटस देखें।"),
    ("Aaj ke liye skip ho gaya.", "Skipped for today.", "आज के लिए छोड़ दिया गया।"),
    ("Nahi ho paya. Page reload karke phir try karein.", "Could not do it. Reload the page and try again.",
     "नहीं हो पाया। पेज रीलोड करके फिर कोशिश करें।"),
    ("Kuch due nahi", "Nothing due", "कुछ बकाया नहीं"),
    ("Aaj kaun ko reminder jayega dekhein", "See who gets a reminder today", "आज किसको रिमाइंडर जाएगा देखें"),
    ("Aaj kaun ko reminder jayega", "Who gets a reminder today", "आज किसको रिमाइंडर जाएगा"),
    ("Aaj holiday hai.", "Today is a holiday.", "आज छुट्टी है।"),
    ("Koi reminder nahi jayega; agle working day chala jayega.",
     "No reminders will go; they move to the next working day.",
     "कोई रिमाइंडर नहीं जाएगा; अगले कार्य दिवस पर चला जाएगा।"),
    ("Aaj kisi ko reminder nahi jayega.", "No reminders will go today.", "आज किसी को रिमाइंडर नहीं जाएगा।"),
    (" parties</b> ko aaj reminder jayega (total ", " parties</b> get a reminder today (total ",
     " पार्टियों</b> को आज रिमाइंडर जाएगा (कुल "),
    (" aaj, baaki agle dino.", " today, the rest over the next days.", " आज, बाकी अगले दिनों में।"),
    ("Load nahi hua.", "Could not load.", "लोड नहीं हुआ।"),
    ("Sab reminder ho gaye", "All reminders done", "सभी रिमाइंडर हो गए"),
    ("Credit days &amp; reminder schedule", "Credit days &amp; reminder schedule",
     "क्रेडिट दिन &amp; रिमाइंडर शेड्यूल"),
    ("Tally se aaya hua, ya galat/khaali ho to yahan set karein.",
     "Comes from Tally; set it here if it is wrong or empty.",
     "टैली से आया है; गलत या खाली हो तो यहाँ सेट करें।"),
    ("Reminder in dino par jayega (bill ke baad):", "Reminders go on these days (after the bill):",
     "रिमाइंडर इन दिनों पर जाएगा (बिल के बाद):"),
    ("Due date ke baad har ~", "After the due date, every ~", "ड्यू डेट के बाद हर ~"),
    ("din ek overdue reminder.", "days one overdue reminder.", "दिन में एक ओवरड्यू रिमाइंडर।"),
    ("(Timing ASVA khud set karta hai. Aap sirf credit days badal sakte hain.)",
     "(ASVA sets the timing itself. You only change the credit days.)",
     "(समय ASVA खुद तय करता है। आप सिर्फ़ क्रेडिट दिन बदल सकते हैं।)"),
    ("Credit days 1 se 730 ke beech likhein.", "Enter credit days between 1 and 730.",
     "क्रेडिट दिन 1 से 730 के बीच लिखें।"),
    ("Abhi ", "Send now to ", "अभी "),
    (" ko reminder bhejein?", " a reminder?", " को रिमाइंडर भेजें?"),
    ("Kitna payment mila? (Rs me)", "How much was received? (in Rs)", "कितना पेमेंट मिला? (रु में)"),
    ("Sahi amount likhein.", "Enter a valid amount.", "सही रकम लिखें।"),
    ("Kuch apply nahi hua.", "Nothing was applied.", "कुछ लागू नहीं हुआ।"),
    (" active customers hain - ", " active customers - ", " सक्रिय ग्राहक हैं - "),
    (" active customers ke liye ", " active customers, ", " सक्रिय ग्राहकों के लिए "),
    ("/month) lein.", "/month).", "/महीना) लें।"),
    (" kaafi hai.", " is enough.", " काफ़ी है।"),
    ("plan sahi hai.", "plan is correct.", "प्लान सही है।"),
    ("Aapka ", "Your ", "आपका "),
    ("Aapke ", "You have ", "आपके "),
    ("ASVA suggestion: ", "ASVA suggestion: ", "ASVA सुझाव: "),
    # ---- Reminder batches ----
    ("Batch = language + UPI + time ka ek group. Dashboard me har party ko batch dein; message apne aap jaate hain.",
     "A batch = a group with its own language, UPI and time. Assign parties to batches on the Dashboard; messages go out automatically.",
     "बैच = अपनी भाषा, UPI और समय वाला एक ग्रुप। डैशबोर्ड पर हर पार्टी को बैच दें; मैसेज अपने आप जाते हैं।"),
    ("Batch = language + UPI + time. Dashboard me har party ko batch dein. Jo assign nahi, unko Batch 1. Discount optional hai (0 = koi nahi).",
     "Batch = language + UPI + time. Assign each party a batch on the Dashboard. Unassigned parties use Batch 1. Discount is optional (0 = none).",
     "बैच = भाषा + UPI + समय। डैशबोर्ड पर हर पार्टी को बैच दें। जो असाइन नहीं, उन्हें बैच 1। छूट वैकल्पिक है (0 = कोई नहीं)।"),
    ("Reminder kaise chalta hai (sab automatic):",
     "How reminders work (all automatic):", "रिमाइंडर कैसे चलता है (सब अपने आप):"),
    ("1. Naya bill = credit period ke andar vinamra yaad (30 din me 5 baar).",
     "1. New bill = polite reminders inside the credit period (5 in 30 days).",
     "1. नया बिल = क्रेडिट अवधि के अंदर विनम्र याद (30 दिन में 5 बार)।"),
    ("2. Due date ke baad = har ~7 din me overdue message, jab tak payment na aaye (max 200 din). Lambi credit walon ko utna hi lamba gap.",
     "2. After the due date = an overdue message every ~7 days until they pay (max 200 days). Longer credit means a longer gap.",
     "2. ड्यू डेट के बाद = हर ~7 दिन में ओवरड्यू मैसेज, जब तक पेमेंट न आए (अधिकतम 200 दिन)। लंबी क्रेडिट वालों को उतना ही लंबा अंतराल।"),
    ("3. Phir ASVA aapko bolega: ab khud call karein.",
     "3. Then ASVA tells YOU: call them yourself now.",
     "3. फिर ASVA आपको बोलेगा: अब खुद कॉल करें।"),
    ("4. Jis din party SELECT karo, ginti usi din se - overdue party ko usi din overdue message jata hai.",
     "4. Counting starts the day you SELECT a party - an overdue party gets the overdue message that same day.",
     "4. जिस दिन पार्टी चुनें, गिनती उसी दिन से - ओवरड्यू पार्टी को उसी दिन ओवरड्यू मैसेज जाता है।"),
    ("Ek party = EK message: saare bills jodkar, total + QR ke saath.",
     "One party = ONE message: all bills combined, with total + QR.",
     "एक पार्टी = एक मैसेज: सारे बिल जोड़कर, कुल + QR के साथ।"),
    ("Aage ki date par tap = holiday (red). Us din reminder skip, agle working day jayega.",
     "Tap a future date = holiday (red). Reminders skip that day and go the next working day.",
     "आगे की तारीख़ पर टैप = छुट्टी (लाल)। उस दिन रिमाइंडर छूटेगा, अगले कार्य दिवस पर जाएगा।"),
    ("Save holidays", "Save holidays", "छुट्टियाँ सेव करें"),
    ("Send Time", "Send Time", "भेजने का समय"),
    ("Batch banayein (tone, language, discount) &rarr; Dashboard me har party ko batch dein &rarr; ASVA khud reminder bhejta hai. Jo assign nahi, unko Batch 1.",
     "Create a batch (tone, language, discount) &rarr; assign each party a batch on the Dashboard &rarr; ASVA sends the reminders. Unassigned parties use Batch 1.",
     "बैच बनाएं (टोन, भाषा, छूट) &rarr; डैशबोर्ड पर हर पार्टी को बैच दें &rarr; ASVA खुद रिमाइंडर भेजता है। जो असाइन नहीं, उन्हें बैच 1।"),
    ("Alag customers ko alag tone, language ya discount dena ho to batches banayein (max 5). Har batch ka apna severity, language, discount aur custom line hota hai. Dashboard se har party ko batch chunein. Jo assign nahi, unko Batch 1 jaata hai. Discount sirf ussi batch me lagta hai jisme aap set karo (0 = koi discount nahi).",
     "To give different customers a different tone, language or discount, create batches (max 5). Each batch has its own severity, language, discount and custom line. Assign each party to a batch from the Dashboard. Unassigned parties use Batch 1. A discount applies only to the batch you set it on (0 = no discount).",
     "अलग ग्राहकों को अलग टोन, भाषा या छूट देनी हो तो बैच बनाएं (अधिकतम 5)। हर बैच का अपना severity, भाषा, छूट और custom line होता है। डैशबोर्ड से हर पार्टी को बैच चुनें। जो असाइन नहीं, उन्हें बैच 1 जाता है। छूट सिर्फ़ उसी बैच में लगती है जिसमें आप सेट करें (0 = कोई छूट नहीं)।"),
    ("Reminder timing ASVA khud manage karta hai (har party ke credit days ke hisaab se). Yahan sirf batches, send time aur holidays set karein.",
     "ASVA manages reminder timing itself (based on each party's credit days). Here you only set batches, send time and holidays.",
     "रिमाइंडर का समय ASVA खुद संभालता है (हर पार्टी के क्रेडिट दिनों के हिसाब से)। यहाँ सिर्फ़ बैच, भेजने का समय और छुट्टियाँ सेट करें।"),
    ("Reminder batches", "Reminder batches", "रिमाइंडर बैच"),
    ("+ Batch add karein", "+ Add batch", "+ बैच जोड़ें"),
    ("Save batches", "Save batches", "बैच सेव करें"),
    ("Save time", "Save time", "समय सेव करें"),
    ("Custom line (optional)", "Custom line (optional)", "Custom line (वैकल्पिक)"),
    ("Har din is time par (jab tak system on hai).", "Every day at this time (while the system is on).",
     "हर दिन इस समय पर (जब तक सिस्टम चालू है)।"),
    ("Batch assign", "Assign batch", "बैच असाइन करें"),
    ("Selected parties ko is batch me daalein", "Move selected parties to this batch",
     "चुनी हुई पार्टियों को इस बैच में डालें"),
    ("Pehle parties tick karein.", "Tick some parties first.", "पहले कुछ पार्टियाँ टिक करें।"),
    ("Pehle ", "First ", "पहले "),
    ("Is batch ka UPI. Khaali = shop default.", "This batch's UPI. Blank = shop default.",
     "इस बैच का UPI। खाली = शॉप डिफ़ॉल्ट।"),
    ("(is party ke reminder ki language, UPI aur time)", "(this party's reminder language, UPI and time)",
     "(इस पार्टी के रिमाइंडर की भाषा, UPI और समय)"),
    ("Batch: 1 (Standard). Alag batches Reminders tab me banayein.",
     "Batch: 1 (Standard). Create more batches in the Reminders tab.",
     "बैच: 1 (Standard)। और बैच Reminders टैब में बनाएं।"),
    # ---- Reminders page ----
    ("Reminder timing ASVA khud manage karta hai (har party ke credit days ke hisaab se). Aap sirf yeh settings badlein.",
     "ASVA manages reminder timing itself (based on each party's credit days). You only change these settings.",
     "रिमाइंडर का समय ASVA खुद संभालता है (हर पार्टी के क्रेडिट दिनों के हिसाब से)। आप सिर्फ़ ये सेटिंग बदलें।"),
    ("Message language", "Message language", "मैसेज की भाषा"),
    ("Send reminders at", "Send reminders at", "रिमाइंडर भेजें"),
    ("Custom line", "Custom line", "Custom line"),
    ("(optional) e.g. Diwali greetings", "(optional) e.g. Diwali greetings", "(वैकल्पिक) जैसे दिवाली की शुभकामनाएं"),
    ("Early-pay discount", "Early-pay discount", "जल्दी-पेमेंट छूट"),
    ("QR + amount is discount se kam ho jayega. 0 = no discount.",
     "QR + amount drop by this discount. 0 = no discount.",
     "QR + रकम इस छूट से कम हो जाएगी। 0 = कोई छूट नहीं।"),
    ("Jo settings chuni hain, wahi message dikhega.", "Shows the message with the settings you chose.",
     "जो सेटिंग चुनी हैं, वही मैसेज दिखेगा।"),
    ("View message", "View message", "मैसेज देखें"),
    ("Save settings", "Save settings", "सेटिंग सेव करें"),
    ("Aaj se aage ki date pe tap karke holiday mark karein (red). Purani dates select nahi hongi. Marked dates par reminder skip hoga aur agle working day chala jayega. Save dabana zaroori hai; last saved list hi final hoti hai.",
     "Tap a future date to mark a holiday (red). Past dates cannot be selected. On marked dates reminders skip and move to the next working day. You must press Save; the last saved list is final.",
     "आज से आगे की तारीख़ पर टैप करके छुट्टी मार्क करें (लाल)। पुरानी तारीख़ें नहीं चुनी जाएंगी। मार्क तारीख़ों पर रिमाइंडर छूटेगा और अगले कार्य दिवस पर जाएगा। Save दबाना ज़रूरी है; आख़िरी सेव लिस्ट ही फ़ाइनल होती है।"),
    ("Message preview", "Message preview", "मैसेज प्रीव्यू"),
    ("Koi holiday nahi. Har din reminder ja sakta hai.", "No holidays. Reminders can go any day.",
     "कोई छुट्टी नहीं। हर दिन रिमाइंडर जा सकता है।"),
    ("Holidays (in dino skip): ", "Holidays (skipped): ", "छुट्टियाँ (इन दिनों छूट): "),
    ("Preview not available.", "Preview not available.", "प्रीव्यू उपलब्ध नहीं।"),
    ("Preview failed.", "Preview failed.", "प्रीव्यू विफल।"),
    # ---- Party page ----
    ("ASVA is party ke credit days ke hisaab se khud reminder bhejta hai.",
     "ASVA sends reminders itself based on this party's credit days.",
     "ASVA इस पार्टी के क्रेडिट दिनों के हिसाब से खुद रिमाइंडर भेजता है।"),
    ("Reminders abhi OFF hain. ON karne par schedule niche dikhega.",
     "Reminders are OFF. Turn them ON to see the schedule below.",
     "रिमाइंडर अभी बंद हैं। चालू करने पर शेड्यूल नीचे दिखेगा।"),
    ("Reminder schedule", "Reminder schedule", "रिमाइंडर शेड्यूल"),
    ("Sabhi reminder ja chuke.", "All reminders already sent.", "सभी रिमाइंडर जा चुके।"),
    ("Ab tak <b>", "So far <b>", "अब तक <b>"),
    ("</b> reminder gaye.", "</b> reminders sent.", "</b> रिमाइंडर गए।"),
    ("Roz bhejne ka time:", "Daily send time:", "रोज़ भेजने का समय:"),
    ("Aane wale:", "Upcoming:", "आने वाले:"),
    # ---- Non-Tally bill add/edit/delete (party page JS prompts) ----
    ("+ Add bill (non Tally)", "+ Add bill (non Tally)", "+ बिल जोड़ें (बिना टैली)"),
    ("Naya bill - amount (Rs):", "New bill - amount (Rs):", "नया बिल - रकम (रु):"),
    ("Bill number (khaali chhodo to auto):", "Bill number (leave empty = automatic):",
     "बिल नंबर (खाली छोड़ें = अपने आप):"),
    ("Sirf number likhein, jaise 12500", "Enter numbers only, e.g. 12500", "सिर्फ़ नंबर लिखें, जैसे 12500"),
    (" DELETE karein? Wapas nahi aayega.", " - delete this bill? This cannot be undone.",
     " - यह बिल हटाएं? यह वापस नहीं आएगा।"),
    ("Nahi ho paya. Phir se try karein.", "Could not save. Please try again.", "नहीं हो पाया। फिर कोशिश करें।"),
    # ---- Nav / update banner ----
    ("Page fresh karein", "Refresh this page", "पेज ताज़ा करें"),
    ("Naya ASVA version <b>", "New ASVA version <b>", "नया ASVA वर्शन <b>"),
    ("</b> aa gaya hai", "</b> is available", "</b> आ गया है"),
    (" Naya zip laga lein.", " Install the new zip to update.", " नया zip लगा लें।"),
    ("(zaroori update)", "(required update)", "(ज़रूरी अपडेट)"),
    ("Reminder OFF karein", "Turn reminder OFF", "रिमाइंडर बंद करें"),
    ("Reminder ON karein", "Turn reminder ON", "रिमाइंडर चालू करें"),
    (" ke reminder OFF kar dein? Isko automatic reminder nahi jayega.",
     " - turn reminders OFF? It will get no automatic reminders.",
     " - रिमाइंडर बंद कर दें? इसे अपने आप रिमाइंडर नहीं जाएगा।"),
    ("Nahi ho paya.", "Could not do it.", "नहीं हो पाया।"),
    ("Koi bill nahi.", "No bills.", "कोई बिल नहीं।"),
    ("Payments received (Tally)", "Payments received (Tally)", "मिले पेमेंट (टैली)"),
    ("Tally me is party ka koi receipt record nahi mila.",
     "No receipt recorded for this party in Tally.",
     "टैली में इस पार्टी का कोई रसीद रिकॉर्ड नहीं मिला।"),
    ("Non-Tally party - payments dashboard se record hote hain.",
     "Non-Tally party - payments are recorded from the dashboard.",
     "बिना-टैली पार्टी - पेमेंट डैशबोर्ड से रिकॉर्ड होते हैं।"),
    ("Total baaki", "Total outstanding", "कुल बकाया"),
    ("Open bills", "Open bills", "खुले बिल"),
    ("Credit days", "Credit days", "क्रेडिट दिन"),
    ("aapko alert", "owner alert", "मालिक अलर्ट"),
    ("Aaj", "Today", "आज"),
    ("Sab ho gaye", "All done", "सब हो गए"),
    ("number nahi hai", "no number", "नंबर नहीं है"),
    # ---- Setup wizard ----
    ("3 chhote step, phir aap live.", "3 quick steps and you are live.", "3 छोटे स्टेप, फिर आप लाइव।"),
    ("Reminder tone", "Reminder tone", "रिमाइंडर टोन"),
    ("Finish setup", "Finish setup", "सेटअप पूरा करें"),
    ('Ho gaya. Ab <b>WhatsApp Setup</b> tab me QR scan karein, phir Dashboard me "Sab ON" karke live ho jayein.',
     'Done. Now scan the QR in the <b>WhatsApp Setup</b> tab, then press "All ON" on the Dashboard to go live.',
     'हो गया। अब <b>WhatsApp Setup</b> टैब में QR स्कैन करें, फिर डैशबोर्ड में "सब चालू" करके लाइव हो जाएं।'),
    # ---- Analytics ----
    ("Collections (last 6 months)", "Collections (last 6 months)", "कलेक्शन (पिछले 6 महीने)"),
    ("Aging (kitne din se baaki)", "Aging (days outstanding)", "एजिंग (कितने दिन से बकाया)"),
    ("Sabse zyada baaki (top 12)", "Highest dues (top 12)", "सबसे ज़्यादा बकाया (टॉप 12)"),
    ("Is month aaya", "Received this month", "इस महीने आया"),
    ("Collection rate", "Collection rate", "कलेक्शन दर"),
    ("Avg DSO", "Avg DSO", "औसत DSO"),
    ("Parties owing", "Parties owing", "बकाया वाली पार्टियाँ"),
    ("Overdue parties", "Overdue parties", "ओवरड्यू पार्टियाँ"),
    ("Open accounts", "Open accounts", "खुले खाते"),
    ("Not due", "Not due", "बकाया नहीं"),
    # ---- Accounts ----
    ("UPI ID (reminder me QR + link isi ka jayega)", "UPI ID (used for the QR + link in reminders)",
     "UPI ID (रिमाइंडर में QR + लिंक इसी का जाएगा)"),
    ("UPI set hai to har reminder me pay-link + QR apne aap lagta hai.",
     "If UPI is set, every reminder gets a pay-link + QR automatically.",
     "UPI सेट है तो हर रिमाइंडर में pay-link + QR अपने आप लगता है।"),
    ("Bank account name", "Bank account name", "बैंक खाता नाम"),
    ("Account number", "Account number", "खाता नंबर"),
    ("Bank name", "Bank name", "बैंक का नाम"),
    ("UPI na ho to reminder me ye bank details (A/C + IFSC) bheji jayengi.",
     "If there is no UPI, these bank details (A/C + IFSC) are sent in reminders.",
     "UPI न हो तो रिमाइंडर में ये बैंक डिटेल (A/C + IFSC) भेजी जाएंगी।"),
    # ---- Shared / small ----
    ("Tally se sync:", "Tally sync:", "टैली से सिंक:"),
    ("(har 5 min auto)", "(every 5 min, auto)", "(हर 5 मिनट, अपने आप)"),
    ("Reload data", "Reload data", "डेटा रीलोड करें"),
    ("Saving...", "Saving...", "सेव हो रहा है..."),
    ("Sending...", "Sending...", "भेजा जा रहा है..."),
    ("Save failed", "Save failed", "सेव विफल"),
    ("Loading...", "Loading...", "लोड हो रहा है..."),
    ("Nahi mila", "Not found", "नहीं मिला"),
    (" din", " days", " दिन"),
    ("Baaki", "Balance", "बकाया"),
    # ---- Strings that used to stay Hinglish in English mode ----
    ("Tally se abhi tak sync nahi", "Not synced with Tally yet", "टैली से अभी तक सिंक नहीं"),
    ("Tally sync ho raha hai...", "Tally is syncing...", "टैली सिंक हो रहा है..."),
    ("Tally se contact nahi (Tally/laptop check karein)", "No contact with Tally (check Tally and the computer)",
     "टैली से संपर्क नहीं (टैली/कंप्यूटर जांचें)"),
    ("abhi tak nahi", "not yet", "अभी तक नहीं"),
    ("abhi abhi", "just now", "अभी अभी"),
    (" min pehle", " min ago", " मिनट पहले"),
    (" ghante pehle", " hours ago", " घंटे पहले"),
    ("Update download karein", "Download the update", "अपडेट डाउनलोड करें"),
    ("Abhi tak koi payment record nahi. Upar &ldquo;Record payment&rdquo; se daalein.",
     "No payments recorded yet. Add one with &ldquo;Record payment&rdquo; above.",
     "अभी तक कोई पेमेंट दर्ज नहीं। ऊपर &ldquo;Record payment&rdquo; से डालें।"),
    (" ko do-not-chase list me daalein? Reminders band ho jayenge.",
     ": put on the do-not-chase list? Reminders will stop.",
     " को डू-नॉट-चेज़ लिस्ट में डालें? रिमाइंडर बंद हो जाएंगे।"),
    (" ko wapas laayein? Woh phir se chase list me aa jayega.",
     ": bring back? They go back on the chase list.",
     " को वापस लाएं? वे फिर से चेज़ लिस्ट में आ जाएंगे।"),
    (" ko wapas chase list me laayein?", ": bring back to the chase list?", " को वापस चेज़ लिस्ट में लाएं?"),
    ("' parties ko Batch '", "' parties: move to Batch '", "' पार्टियों को बैच '"),
    ("' me daalein?'", "'?'", "' में डालें?'"),
    ("UPI ID 1 (Default - reminder me QR + link isi ka jayega)",
     "UPI ID 1 (default: reminders carry this QR and link)",
     "UPI ID 1 (डिफ़ॉल्ट - रिमाइंडर में इसी का QR + लिंक जाएगा)"),
    ("(reminder me QR + link isi ka jayega)", "(reminders carry this QR and link)",
     "(रिमाइंडर में इसी का QR + लिंक जाएगा)"),
    ("1. Aap kis line me ho? (Your trade)", "1. Your trade", "1. आपका व्यापार"),
    (" - chhote step, phir aap live.", ": a few short steps, then you are live.", " - छोटे स्टेप, फिर आप लाइव।"),
    ("Isse un customers ke liye credit din set hote hain jinke Tally me credit period nahi hai. "
     "Electricals = 60 din, aur bhi. Tally me jahan period hai wahan wahi chalega.",
     "This sets credit days for customers with no credit period in Tally (for example, "
     "electricals = 60 days). Where Tally has a period, Tally wins.",
     "इससे उन ग्राहकों के क्रेडिट दिन तय होते हैं जिनका टैली में क्रेडिट पीरियड नहीं है "
     "(जैसे इलेक्ट्रिकल्स = 60 दिन)। जहाँ टैली में पीरियड है, वहाँ वही चलेगा।"),
    ("Tally party - naam aur number Tally se aate hain.", "Tally party: name and number come from Tally.",
     "टैली पार्टी - नाम और नंबर टैली से आते हैं।"),
]

# Longest-source-first so a short source never eats a longer one mid-replace.
_UI_SORTED = sorted(UI, key=lambda t: len(t[0]), reverse=True)
_COL = {"english": 1, "hindi": 2}   # hinglish is the source (no-op)


def translate_html(html: str, lang: str) -> str:
    """Rewrite a Hinglish-authored admin page into the chosen locale."""
    col = _COL.get(norm(lang))
    if col is None:                 # hinglish -> the page is already correct
        return html
    for row in _UI_SORTED:
        src = row[0]
        dst = row[col]
        if src != dst:
            html = html.replace(src, dst)
    return html


# ── CLIENT-screen dictionary: English key -> {hinglish, hindi} ────────────────
# For Today and the phone PWA, whose visible text is built in JavaScript. The JS
# calls TT('<english>'); english is the identity locale.
JS: dict[str, dict[str, str]] = {
    # -- Today: hero cards --
    "Money in yesterday": {"hinglish": "Kal aaya paisa", "hindi": "कल आया पैसा"},
    "Coming in": {"hinglish": "Aane wala", "hindi": "आने वाला"},
    "days": {"hinglish": "din", "hindi": "दिन"},
    "Recovered": {"hinglish": "Wasool hua", "hindi": "वसूल हुआ"},
    "this month": {"hinglish": "is month", "hindi": "इस महीने"},
    "Money stuck": {"hinglish": "Atka paisa", "hindi": "अटका पैसा"},
    "Today so far": {"hinglish": "Aaj ab tak", "hindi": "आज अब तक"},
    "payment": {"hinglish": "payment", "hindi": "पेमेंट"},
    "payments": {"hinglish": "payments", "hindi": "पेमेंट"},
    "booked": {"hinglish": "book hua", "hindi": "दर्ज हुआ"},
    "No payments booked yet": {"hinglish": "Abhi tak koi payment nahi", "hindi": "अभी तक कोई पेमेंट नहीं"},
    "promised": {"hinglish": "vaada", "hindi": "वादा"},
    "due": {"hinglish": "due", "hindi": "बकाया"},
    "Nothing promised yet": {"hinglish": "Abhi tak koi vaada nahi", "hindi": "अभी तक कोई वादा नहीं"},
    "Up": {"hinglish": "Zyada", "hindi": "ज़्यादा"},
    "Down": {"hinglish": "Kam", "hindi": "कम"},
    "vs last month": {"hinglish": "pichhle month se", "hindi": "पिछले महीने से"},
    "parties owe you": {"hinglish": "parties ka baaki hai", "hindi": "पार्टियों का बकाया है"},
    "across your open bills": {"hinglish": "aapke khule bills me", "hindi": "आपके खुले बिलों में"},
    # -- Today: sections --
    "Who to chase today": {"hinglish": "Aaj kaun se chase karein", "hindi": "आज किससे वसूली करें"},
    "Top": {"hinglish": "Top", "hindi": "टॉप"},
    "by money at stake": {"hinglish": "sabse zyada paise wale", "hindi": "सबसे ज़्यादा पैसे वाले"},
    "of": {"hinglish": "me se", "hindi": "में से"},
    "overdue": {"hinglish": "overdue", "hindi": "ओवरड्यू"},
    "d late": {"hinglish": "d late", "hindi": " दिन देर"},
    "days past due": {"hinglish": "din se overdue", "hindi": "दिन से ओवरड्यू"},
    "no WhatsApp number": {"hinglish": "koi WhatsApp number nahi", "hindi": "कोई WhatsApp नंबर नहीं"},
    "WhatsApp": {"hinglish": "WhatsApp", "hindi": "WhatsApp"},
    "Call": {"hinglish": "Call", "hindi": "कॉल"},
    "Remind": {"hinglish": "Yaad dilayein", "hindi": "याद दिलाएं"},
    "Add number": {"hinglish": "Number jodein", "hindi": "नंबर जोड़ें"},
    "Nothing overdue right now. When bills cross their due date, the ones worth chasing show here, biggest first.":
        {"hinglish": "Abhi kuch overdue nahi. Jab bill due date paar karenge, chase karne layak yahan dikhenge, sabse bade pehle.",
         "hindi": "अभी कुछ ओवरड्यू नहीं। जब बिल ड्यू डेट पार करेंगे, वसूली लायक यहाँ दिखेंगे, सबसे बड़े पहले।"},
    "Said they will pay": {"hinglish": "Bola pay karenge", "hindi": "बोले पेमेंट करेंगे"},
    "Following up automatically. Enter the payment when it lands.":
        {"hinglish": "Apne aap follow-up ho raha hai. Payment aane par enter karein.",
         "hindi": "अपने आप फ़ॉलो-अप हो रहा है। पेमेंट आने पर दर्ज करें।"},
    "By": {"hinglish": "Tak", "hindi": "तक"},
    "Enter payment": {"hinglish": "Payment enter karein", "hindi": "पेमेंट दर्ज करें"},
    "No open promises. When a customer says they paid or gives a date, it shows here.":
        {"hinglish": "Koi khula vaada nahi. Jab customer kahe ki pay kiya ya date de, yahan dikhega.",
         "hindi": "कोई खुला वादा नहीं। जब ग्राहक कहे कि पेमेंट किया या तारीख़ दे, यहाँ दिखेगा।"},
    "Where your money is stuck": {"hinglish": "Aapka paisa kahan atka hai", "hindi": "आपका पैसा कहाँ अटका है"},
    "Outstanding by how far past due.":
        {"hinglish": "Kitne din overdue, uske hisaab se baaki.", "hindi": "कितने दिन ओवरड्यू, उसके हिसाब से बकाया।"},
    "No open bills.": {"hinglish": "Koi khula bill nahi.", "hindi": "कोई खुला बिल नहीं।"},
    # -- Today: nudges + states --
    "customer": {"hinglish": "customer", "hindi": "ग्राहक"},
    "customers": {"hinglish": "customers", "hindi": "ग्राहक"},
    "Add their number to start reminders and recover more.":
        {"hinglish": "Reminder shuru karne aur zyada wasool karne ke liye unka number jodein.",
         "hindi": "रिमाइंडर शुरू करने और ज़्यादा वसूलने के लिए उनका नंबर जोड़ें।"},
    "Add numbers": {"hinglish": "Number jodein", "hindi": "नंबर जोड़ें"},
    "Loading...": {"hinglish": "Load ho raha hai...", "hindi": "लोड हो रहा है..."},
    "Loading your day...": {"hinglish": "Aapka din load ho raha hai...", "hindi": "आपका दिन लोड हो रहा है..."},
    "Could not load your day.": {"hinglish": "Aapka din load nahi hua.", "hindi": "आपका दिन लोड नहीं हुआ।"},
    "Retry": {"hinglish": "Phir try karein", "hindi": "फिर कोशिश करें"},
    "Could not refresh just now. Showing your last view.":
        {"hinglish": "Abhi refresh nahi hua. Aapka pichhla view dikha rahe hain.",
         "hindi": "अभी रीफ़्रेश नहीं हुआ। आपका पिछला व्यू दिखा रहे हैं।"},
    "Something went wrong drawing this page.":
        {"hinglish": "Yeh page dikhane me kuch gadbad hui.", "hindi": "यह पेज दिखाने में कुछ गड़बड़ हुई।"},
    "Reload": {"hinglish": "Reload karein", "hindi": "रीलोड करें"},
    "Display hiccup - press Refresh view if it looks off.":
        {"hinglish": "Display me dikkat - theek na dikhe to Refresh dabayein.",
         "hindi": "डिस्प्ले में दिक़्क़त - ठीक न दिखे तो रीफ़्रेश दबाएं।"},
    "Sending...": {"hinglish": "Bhej rahe hain...", "hindi": "भेजा जा रहा है..."},
    "Reminder sent to": {"hinglish": "Reminder gaya", "hindi": "रिमाइंडर गया"},
    "Could not send. Try again.": {"hinglish": "Nahi bheja gaya. Phir try karein.", "hindi": "नहीं भेजा गया। फिर कोशिश करें।"},
    "Shop WhatsApp not connected. Open WhatsApp Setup and scan the QR.":
        {"hinglish": "Shop WhatsApp connect nahi hai. WhatsApp Setup kholkar QR scan karein.",
         "hindi": "शॉप WhatsApp कनेक्ट नहीं है। WhatsApp Setup खोलकर QR स्कैन करें।"},
    "Add WhatsApp numbers": {"hinglish": "WhatsApp number jodein", "hindi": "WhatsApp नंबर जोड़ें"},
    "These customers owe you but have no number saved. Add a 10-digit mobile to start reminding them. A number in Tally always wins later.":
        {"hinglish": "In customers ka baaki hai par number save nahi. Reminder shuru karne ke liye 10-digit mobile jodein. Baad me Tally ka number hamesha chalega.",
         "hindi": "इन ग्राहकों का बकाया है पर नंबर सेव नहीं। रिमाइंडर शुरू करने के लिए 10-अंकों का मोबाइल जोड़ें। बाद में टैली का नंबर हमेशा चलेगा।"},
    "Done": {"hinglish": "Ho gaya", "hindi": "हो गया"},
    "owes": {"hinglish": "baaki", "hindi": "बकाया"},
    "10-digit mobile": {"hinglish": "10-digit mobile", "hindi": "10-अंकों का मोबाइल"},
    "Save": {"hinglish": "Save karein", "hindi": "सेव करें"},
    "Saved": {"hinglish": "Save ho gaya", "hindi": "सेव हो गया"},
    "Everyone who owes you has a number. Nice.":
        {"hinglish": "Jinka baaki hai sabke number hain. Badhiya.", "hindi": "जिनका बकाया है सबके नंबर हैं। बढ़िया।"},
    # -- phone PWA --
    "View only": {"hinglish": "Sirf dekhne ke liye", "hindi": "सिर्फ़ देखने के लिए"},
    "changes are made on the shop computer": {"hinglish": "badlav shop computer par hote hain", "hindi": "बदलाव शॉप कंप्यूटर पर होते हैं"},
    "Get the computer app": {"hinglish": "Computer app lein", "hindi": "कंप्यूटर ऐप लें"},
}


def s(lang: str, key: str) -> str:
    """Localise one client-catalog string on the SERVER (for the few static
    English bits a page paints before its JS runs). English = identity; a
    missing translation falls back to the English key, never to blank."""
    lang = norm(lang)
    if lang == "english":
        return key
    tr = JS.get(key)
    return (tr and tr.get(lang)) or key


def js_map(lang: str) -> dict[str, str]:
    """The active-locale value for every JS key (english = identity)."""
    lang = norm(lang)
    if lang == "english":
        return {}          # identity: TT() returns the key itself
    out = {}
    for key, tr in JS.items():
        v = tr.get(lang)
        if v:
            out[key] = v
    return out


def js_runtime(lang: str) -> str:
    """A <script> body defining LANG + a TT(key) translator for client screens.
    TT returns the localised string, or the English key unchanged if unmapped -
    so a missing translation degrades to English, never to blank."""
    lang = norm(lang)
    data = json.dumps(js_map(lang), ensure_ascii=False)
    return ("const LANG=%s;const I18N=%s;"
            "function TT(s){return (I18N&&I18N[s])||s;}") % (json.dumps(lang), data)
