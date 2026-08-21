"""Owner-approved escalation - NOT automatic.

Deliberate product decision: ASVA never makes its reminder tone harsher on its
own. Every cadence reminder stays the same polite message; the cadence (e.g. a
reminder every 7 days while overdue) is what repeats, not the harshness. A firmer,
formal message goes out ONLY when the OWNER explicitly triggers it (LETTER
<name>). Debt-collection messaging in India is constrained (harassment /
defamation), so anything stronger than a normal reminder stays owner-approved and
is never an automated legal threat.

`tier_for` is a read-only severity LABEL (used to gently flag very-overdue parties
to the owner so THEY can decide to send a letter). It never changes what a
customer receives automatically. Pure functions; no I/O.
"""
from __future__ import annotations

GENTLE, STANDARD, FIRM, FINAL = "gentle", "standard", "firm", "final"
_ORDER = [GENTLE, STANDARD, FIRM, FINAL]


def tier_for(days_overdue: int | None, promise_broken: bool = False) -> str:
    """A severity LABEL from days past due (for flagging to the OWNER only, never
    to auto-change a customer message). A broken promise labels at least 'firm'."""
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
