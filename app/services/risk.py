"""Credit-risk WATCH: a calm, PULL-ONLY read of how exposed the shop is to ONE
party, from data ASVA already has (outstanding + oldest overdue + the reliability
grade). Deliberately quiet by design:

  - It NEVER blocks a sale. ASVA reads Tally read-only, after the voucher, so it
    can only inform - never gate.
  - It is NEVER pushed. No badge on lists, no alert when a new bill syncs, no
    popup. In a collections app most parties are overdue, so flagging everywhere
    would be pure noise. This appears ONLY on the party page, where the owner has
    deliberately opened one customer - exactly the "should I give more credit?"
    moment - and only when there is a real reason for caution.

So an ordinary party's page stays silent; only genuinely over-exposed ones show a
single calm advisory line.
"""
from __future__ import annotations

from app.services import scorecard as _sc

LOW = "low"
WATCH = "watch"
HIGH = "high"

# Muted, calm colours (amber for watch, soft red for high) - never alarming.
COLOR = {LOW: "#6b7770", WATCH: "#7a5200", HIGH: "#9f2f2d"}
BG = {WATCH: "#fbf3db", HIGH: "#fdebec"}
BORDER = {WATCH: "#efdfa8", HIGH: "#f3c9c7"}


def assess(outstanding, max_overdue_days, grade: str | None = None) -> dict:
    """Return {'level', 'advisory'}.

    advisory is a calm one-liner shown ONLY when caution is genuinely warranted;
    '' otherwise, so the party page stays quiet for the many ordinary parties.
    Thresholds are conservative (nothing fires under ~45 days overdue) so the
    signal stays rare and meaningful, never alert-fatigue.

    Days-overdue is the universal signal (amounts vary hugely by shop); a poor
    reliability grade lowers the bar a little."""
    amt = float(outstanding or 0)
    od = int(max_overdue_days or 0)
    slow = grade in (_sc.GRADE_RISKY, _sc.GRADE_WATCH)
    if amt <= 0:
        return {"level": LOW, "advisory": ""}
    if od >= 90 or (slow and od >= 60):
        return {"level": HIGH,
                "advisory": f"High exposure: oldest bill is {od} days overdue. "
                            f"Best to collect before extending more credit."}
    if od >= 45 or (slow and od >= 30):
        return {"level": WATCH,
                "advisory": f"Watch: oldest bill is {od} days overdue. "
                            f"Keep an eye before giving more credit."}
    return {"level": LOW, "advisory": ""}
