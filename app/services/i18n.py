"""Owner-facing language: English vs Hinglish, one setting per business.

The owner picks a language in the ASVA app; it is saved on the business row
(owner_language) and read here so the WhatsApp bot answers the owner in the SAME
language as the app - "if English is chosen, pure English everywhere". The
CUSTOMER-facing reminder language is a SEPARATE axis (per reminder batch) and is
deliberately not touched by this.

t(lang, key, **kw) returns the owner-facing string for a key. Unknown keys or
langs fall back safely (english, then the raw key) so a missing translation never
crashes a reply - it just shows English.
"""
from __future__ import annotations

ENGLISH = "english"
HINGLISH = "hinglish"
LANGS = (ENGLISH, HINGLISH)

_ALIASES = {
    "en": ENGLISH, "eng": ENGLISH, "english": ENGLISH,
    "hi": HINGLISH, "hin": HINGLISH, "hindi": HINGLISH, "hinglish": HINGLISH,
}


def norm_lang(v) -> str:
    """Any stored/typed value -> a known language, defaulting to English."""
    return _ALIASES.get(str(v or "").strip().lower(), ENGLISH)


def is_english(v) -> bool:
    return norm_lang(v) == ENGLISH


# Owner-facing strings. Keep both languages plain and short; no em/en dashes.
_CATALOG: dict[str, dict[str, str]] = {
    "unknown_prefix": {
        ENGLISH: "Sorry, I did not understand that. Here is what I can do:\n\n",
        HINGLISH: "Maaf kijiye, samajh nahi aaya. Yeh main kar sakta hoon:\n\n",
    },
    "help": {
        ENGLISH: (
            "*ASVA*, your collection helper.\n"
            "Send a command with a party's name. Ramesh below is only an example.\n\n"
            "*SEE YOUR MONEY*\n"
            "*LIST*: everyone who owes you\n"
            "*CHECK Ramesh*: one party's balance\n"
            "*RECOVERED*: what ASVA got back this month\n"
            "*DIGEST*: today's summary\n"
            "*SENT*: who was reminded today\n"
            "*PROMISES*: who said they will pay\n\n"
            "*GET PAID*\n"
            "*REMIND Ramesh*: remind one party now\n"
            "*REMIND TOP 10*: chase the 10 biggest\n"
            "*BILL Ramesh 12500*: add a bill, or send its photo\n"
            "*PAID Ramesh*: mark a payment received\n"
            "*CHASE Ramesh*: resume a party on hold\n\n"
            "*MANAGE A PARTY*\n"
            "*STOP Ramesh*: pause reminders (START to resume)\n"
            "*EXCLUDE Ramesh*: never chase (INCLUDE to undo)\n"
            "*TERMS Ramesh 45*: set credit days\n\n"
            "Need help? Send *TEAM* with your message."
        ),
        HINGLISH: (
            "*ASVA*, aapka collection helper.\n"
            "Kisi party ke naam ke saath command bhejein. Neeche Ramesh sirf example hai.\n\n"
            "*APNA PAISA DEKHEIN*\n"
            "*LIST*: jinke paise aapke paas baaki hain\n"
            "*CHECK Ramesh*: ek party ka balance\n"
            "*RECOVERED*: is mahine kitna wapas aaya\n"
            "*DIGEST*: aaj ka summary\n"
            "*SENT*: aaj kisko reminder gaya\n"
            "*PROMISES*: kisne kaha paise denge\n\n"
            "*PAISE PAAYEIN*\n"
            "*REMIND Ramesh*: ek party ko abhi reminder bhejein\n"
            "*REMIND TOP 10*: 10 sabse bade ko chase karein\n"
            "*BILL Ramesh 12500*: bill jodein, ya uski photo bhejein\n"
            "*PAID Ramesh*: payment mila hua mark karein\n"
            "*CHASE Ramesh*: hold par rakhi party ko dobara shuru karein\n\n"
            "*PARTY MANAGE KAREIN*\n"
            "*STOP Ramesh*: reminder roken (START se dobara shuru)\n"
            "*EXCLUDE Ramesh*: kabhi chase na karein (INCLUDE se wapas)\n"
            "*TERMS Ramesh 45*: credit din set karein\n\n"
            "Madad chahiye? *TEAM* ke saath apna message bhejein."
        ),
    },
    # A few high-traffic replies, so the pipeline is proven end to end. The rest
    # of the bot's owner replies move onto t() as commands are wired.
    "which_one": {
        ENGLISH: "'{q}' matches more than one: {list}. Type the fuller name.",
        HINGLISH: "'{q}' se ek se zyada party milti hain: {list}. Poora naam likhein.",
    },
    "no_match": {
        ENGLISH: "No party matches '{q}'. Send LIST to see the names.",
        HINGLISH: "'{q}' se koi party nahi mili. Naam dekhne ke liye LIST bhejein.",
    },
    "stopped": {
        ENGLISH: "Stopped reminders for {name}. Reply START {name} to resume.",
        HINGLISH: "{name} ke reminder rok diye. Dobara shuru karne ke liye START {name} bhejein.",
    },
    "already_off": {
        ENGLISH: "{name}'s reminders are already off.",
        HINGLISH: "{name} ke reminder pehle se hi band hain.",
    },
    "started": {
        ENGLISH: "Reminders resumed for {name}.",
        HINGLISH: "{name} ke reminder dobara shuru kar diye.",
    },
    "already_on": {
        ENGLISH: "{name}'s reminders are already on.",
        HINGLISH: "{name} ke reminder pehle se hi chalu hain.",
    },
    "excluded_on": {
        ENGLISH: "{name} is on your do-not-chase list. No more reminders, and they will not show in your daily list. Send INCLUDE {name} to undo.",
        HINGLISH: "{name} ab do-not-chase list par hai. Koi reminder nahi, aur roz ki list mein bhi nahi aayega. Wapas laane ke liye INCLUDE {name} bhejein.",
    },
    "excluded_off": {
        ENGLISH: "{name} is back on. They will be reminded again.",
        HINGLISH: "{name} wapas chalu. Ab dobara reminder jaayenge.",
    },
    "chase_resumed": {
        ENGLISH: "Reminders for {name} have resumed.",
        HINGLISH: "{name} ke reminder dobara shuru ho gaye.",
    },
    "chase_none": {
        ENGLISH: "{name} is not on a promise hold, so there is nothing to resume.",
        HINGLISH: "{name} kisi promise hold par nahi hai, isliye resume karne ke liye kuch nahi hai.",
    },
    "terms_set": {
        ENGLISH: "{name}'s credit period is now {days} days. {updated} open bills got a new due date. Reminders will follow the new period.",
        HINGLISH: "{name} ka credit period ab {days} din hai. {updated} open bills ki nayi due date lag gayi. Reminder naye period ke hisaab se jaayenge.",
    },
    "terms_range": {
        ENGLISH: "Credit days must be between 1 and 365.",
        HINGLISH: "Credit din 1 se 365 ke beech hone chahiye.",
    },
    "recovered": {
        ENGLISH: "ASVA recovered {this} for you in {month}. {out} is still outstanding.{delta}",
        HINGLISH: "ASVA ne {month} mein aapke {this} wapas dilaye. {out} abhi baaki hai.{delta}",
    },
    "recovered_delta": {
        ENGLISH: "\nLast month: {last}.",
        HINGLISH: "\nPichhle mahine: {last}.",
    },
    "recovered_zero": {
        ENGLISH: "No payments recorded yet this month. {out} is outstanding. As receipts come into your Tally, ASVA will show what it helped bring back here.",
        HINGLISH: "Is mahine abhi tak koi payment record nahi hui. {out} baaki hai. Jaise jaise Tally mein receipt aayenge, ASVA yahaan dikhayega ki kitna wapas aaya.",
    },
}


def t(lang, key: str, **kw) -> str:
    """The owner-facing string for `key` in `lang`, formatted with `kw`."""
    lang = norm_lang(lang)
    entry = _CATALOG.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry.get(ENGLISH) or key
    return s.format(**kw) if kw else s
