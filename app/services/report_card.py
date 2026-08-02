"""Monthly Recovery Report Card: the proof that renews subscriptions.

Once a month (and on demand), ASVA tells the owner in plain numbers what it
brought back: money recovered this month, how that compares to last month, how
many payments it booked, and what is still out. This is the single most
important message ASVA sends - it is why an owner pays again next year, and what
they screenshot to a friend. Honest numbers only (all from proof/tally_receipts);
never an invented figure.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

from app.services import proof
from app.services.templates import inr

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _payments_count(db, business_id: str, start: _dt.date, end: _dt.date) -> int:
    """How many receipts ASVA booked in [start, end). Best-effort -> 0."""
    try:
        rows = (db.table("tally_receipts").select("id")
                .eq("business_id", business_id)
                .gte("receipt_date", start.isoformat())
                .lt("receipt_date", end.isoformat()).execute()).data or []
        return len(rows)
    except Exception:
        return 0


def build_card(db, business_id: str, business_name: str, *,
               en: bool = True, today: _dt.date | None = None) -> str:
    """The report-card message text (WhatsApp-ready). Works for the month-end
    auto-send and the on-demand bot command alike."""
    today = today or _dt.datetime.now(IST).date()
    pf = proof.build_proof(db, business_id, today)
    this = Decimal(str(pf.get("recovered_this_month") or 0))
    last = Decimal(str(pf.get("recovered_last_month") or 0))
    out = Decimal(str(pf.get("outstanding") or 0))
    month = pf.get("month", "")

    m_start = today.replace(day=1)
    n_pay = _payments_count(db, business_id, m_start, today + _dt.timedelta(days=1))

    biz = business_name or "Your shop"
    if en:
        lines = [f"*ASVA Report Card - {month}*", biz, ""]
    else:
        lines = [f"*ASVA Report Card - {month}*", biz, ""]

    if this > 0:
        lines.append((f"Recovered this month: {inr(this)}" if en
                      else f"Is mahine wapas aaya: {inr(this)}"))
        if n_pay > 0:
            lines.append((f"Payments booked: {n_pay}" if en
                          else f"Payments Tally mein: {n_pay}"))
        # Trend vs last month - only when there is something to compare to.
        if last > 0:
            if this >= last:
                up = this - last
                lines.append((f"Up {inr(up)} from last month ({inr(last)})" if en
                              else f"Pichhle mahine se {inr(up)} zyada ({inr(last)})"))
            else:
                lines.append((f"Last month: {inr(last)}" if en
                              else f"Pichhla mahina: {inr(last)}"))
    else:
        lines.append((f"No payments were booked into Tally this month yet." if en
                      else "Is mahine abhi tak koi payment Tally mein nahi aaya."))

    lines += ["", (f"Still outstanding: {inr(out)}" if en
                   else f"Abhi baaki: {inr(out)}")]

    # A short, honest closing line: encouragement if it worked, a nudge if not.
    if this > 0:
        lines += ["", ("That is money ASVA helped bring back. Keep it going!" if en
                       else "Yeh paisa ASVA ne wapas dilane mein madad ki. Aise hi chalta rahe!")]
    else:
        lines += ["", ("Record payments in Tally as they come, and next month's "
                       "card will show the total ASVA helped recover." if en
                       else "Payments Tally mein daalte rahein, agle mahine card mein "
                       "poora total dikhega jo ASVA ne wapas dilaya.")]
    return "\n".join(lines)
