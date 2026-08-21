"""Escalation ladder: the reminder tone firms up as an invoice ages.

Deliberately *light* and owner-safe:
- Tone escalates gentle -> standard -> firm -> final with days past due.
- A broken promise bumps the tier up (to at least 'firm').
- The top rung is a FINAL reminder in the owner's own voice, and an
  owner-triggered formal reminder LETTER (see formal_letter_text) - NEVER an
  automated legal threat. Debt-collection messaging in India is constrained
  (harassment / defamation), so anything stronger stays owner-approved.

Pure functions, no I/O, so they are trivially testable and cannot break a send.
"""
from __future__ import annotations

GENTLE, STANDARD, FIRM, FINAL = "gentle", "standard", "firm", "final"
_ORDER = [GENTLE, STANDARD, FIRM, FINAL]


def tier_for(days_overdue: int | None, promise_broken: bool = False) -> str:
    """Pick the tone tier from how many days past due the oldest bill is.
    A broken promise escalates to at least 'firm'."""
    d = int(days_overdue or 0)
    if d <= 7:
        t = GENTLE
    elif d <= 21:
        t = STANDARD
    elif d <= 45:
        t = FIRM
    else:
        t = FINAL
    if promise_broken and _ORDER.index(t) < _ORDER.index(FIRM):
        t = FIRM
    return t


def intro_line(tier: str, biz: str, en: bool) -> str:
    """The header line under the greeting. Tone rises with the tier; still
    respectful at every rung."""
    if en:
        return {
            GENTLE:   f"A payment reminder from {biz}.",
            STANDARD: f"A reminder from {biz}: this payment is now overdue.",
            FIRM:     f"From {biz}: this payment is well past due. Please arrange it at the earliest.",
            FINAL:    f"From {biz}: a final reminder. Please clear this now to avoid further follow-up.",
        }[tier]
    return {
        GENTLE:   f"{biz} ki taraf se payment ka vinamra reminder.",
        STANDARD: f"{biz} ki taraf se reminder: yeh payment ab overdue ho gaya hai.",
        FIRM:     f"{biz} ki taraf se: yeh payment kaafi overdue hai. Kripya jaldi arrange karein.",
        FINAL:    f"{biz} ki taraf se: aakhri reminder. Aage follow-up se bachne ke liye kripya abhi clear karein.",
    }[tier]


def closing_line(tier: str, en: bool) -> str:
    """An extra firm nudge for the top two rungs only. Empty for gentle/standard
    (so an on-time-ish party never gets a hard line)."""
    if tier == FIRM:
        return "Please treat this as urgent." if en else "Kripya ise urgent samjhein."
    if tier == FINAL:
        return "Kindly clear this immediately." if en else "Kripya ise turant clear karein."
    return ""


def formal_letter_text(shop: str, party: str, amount: str, days_overdue: int,
                       *, en: bool = False) -> str:
    """An owner-approved FORMAL reminder letter (owner triggers it with LETTER
    <name>). Firm and businesslike, but NOT a legal threat - it invites the
    customer to pay or raise a concern, and tells them to ignore it if already
    paid."""
    d = int(days_overdue or 0)
    if en:
        return (
            f"Subject: Payment reminder from {shop}\n\n"
            f"Dear {party},\n\n"
            f"Our records show an outstanding amount of {amount}"
            + (f", now {d} days past due" if d > 0 else "") + ".\n\n"
            f"We request you to kindly arrange the payment at your earliest "
            f"convenience. If you have already paid, please share the details and "
            f"ignore this message.\n\n"
            f"For any query about this balance, please reply here and we will "
            f"assist. Thank you for your business.\n\n{shop}")
    return (
        f"Vishay: {shop} ki taraf se payment reminder\n\n"
        f"Aadarniya {party},\n\n"
        f"Hamare record ke anusaar {amount} baaki hai"
        + (f", jo ab {d} din overdue hai" if d > 0 else "") + ".\n\n"
        f"Kripya jald se jald payment arrange karein. Agar aap pehle hi pay kar "
        f"chuke hain, to details bhej dein aur is message ko ignore karein.\n\n"
        f"Is balance ke baare mein koi sawaal ho to yahin reply karein, hum madad "
        f"karenge. Aapke vyapaar ke liye dhanyavaad.\n\n{shop}")
