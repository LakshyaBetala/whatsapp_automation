"""New bills reach the customer on WhatsApp 5-10 minutes after Tally syncs them.

Until October 2026 a bill went out only if the owner exported its PDF into a
folder by hand, so in practice almost none did (4 of 1,299). Now every fresh
sales bill for a customer with a WhatsApp number is noticed:

  - 10 minutes after the bill syncs it goes out as a short text: invoice
    number, date, amount, due date, the customer's total outstanding, and the
    pay link. (A bill whose PDF the agent did attach still goes the old way,
    with the PDF.)
  - More bills for the same customer inside the window join the same message:
    each new one moves the send to +10 minutes, never later than 30 minutes
    after the first.
  - At most one bill message per customer per 30 minutes (ban safety without
    ever dropping a bill: later bills ride in the next message).
  - Quiet hours 22:00-08:00 IST: a bill made at 23:00 goes at 08:00.
  - Only fresh bills (invoice date within 2 days): the first sync replays the
    whole financial year and must not message customers about old invoices.

schedule() is called from the Tally sync; app/jobs/bill_notice_sweep.py sends.
Every DB step is best-effort: a notice problem never breaks a sync.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

from app.services.templates import inr

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
MERGE = _dt.timedelta(minutes=10)
MERGE_CAP = _dt.timedelta(minutes=30)
MIN_GAP = _dt.timedelta(minutes=30)
QUIET_FROM, QUIET_TO = 22, 8          # IST hours
FRESH_DAYS = 2
MAX_LISTED = 6


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _out_of_quiet(t: _dt.datetime) -> _dt.datetime:
    local = t.astimezone(IST)
    if local.hour >= QUIET_FROM:
        nxt = (local + _dt.timedelta(days=1)).replace(hour=QUIET_TO, minute=0, second=0, microsecond=0)
        return nxt.astimezone(t.tzinfo)
    if local.hour < QUIET_TO:
        return local.replace(hour=QUIET_TO, minute=0, second=0, microsecond=0).astimezone(t.tzinfo)
    return t


def next_send_time(now: _dt.datetime, *, first_at: _dt.datetime | None = None,
                   last_sent_at: _dt.datetime | None = None) -> _dt.datetime:
    t = now + MERGE
    if first_at is not None:
        t = max(now, min(t, first_at + MERGE_CAP))
    if last_sent_at is not None:
        t = max(t, last_sent_at + MIN_GAP)
    return _out_of_quiet(t)


def is_fresh(invoice_date: _dt.date, today: _dt.date) -> bool:
    return invoice_date >= today - _dt.timedelta(days=FRESH_DAYS)


def _d(value) -> str:
    try:
        return _dt.date.fromisoformat(str(value)[:10]).strftime("%d %b")
    except (TypeError, ValueError):
        return "-"


def compose(name: str, biz_name: str, bills: list, total_outstanding, *, en: bool,
            pay_link: str | None) -> str:
    """The customer's message for one or more new bills. No em/en dashes."""
    shown, more = bills[:MAX_LISTED], max(0, len(bills) - MAX_LISTED)
    if en:
        head = [f"Dear {name},",
                f"{'A new bill' if len(bills) == 1 else str(len(bills)) + ' new bills'} from {biz_name}:", ""]
        rows = [f"Bill {b.get('invoice_number') or '-'} of {_d(b.get('invoice_date'))}: "
                f"{inr(Decimal(str(b.get('amount') or 0)))}, due {_d(b.get('due_date'))}"
                for b in shown]
        if more:
            rows.append(f"and {more} more")
        tail = ["", f"Total outstanding now: {inr(Decimal(str(total_outstanding or 0)))}"]
        if pay_link:
            tail += ["", "Pay online (UPI, GPay, PhonePe or bank transfer):", pay_link]
        tail += ["", "Thank you."]
    else:
        head = [f"Namaste {name} ji,",
                f"{biz_name} se aapka {'naya bill' if len(bills) == 1 else str(len(bills)) + ' naye bill'}:", ""]
        rows = [f"Bill {b.get('invoice_number') or '-'} ({_d(b.get('invoice_date'))}): "
                f"{inr(Decimal(str(b.get('amount') or 0)))}, due {_d(b.get('due_date'))}"
                for b in shown]
        if more:
            rows.append(f"aur {more} bill")
        tail = ["", f"Kul baaki ab: {inr(Decimal(str(total_outstanding or 0)))}"]
        if pay_link:
            tail += ["", "Online payment (UPI, GPay, PhonePe ya bank transfer):", pay_link]
        tail += ["", "Dhanyavaad."]
    return "\n".join(head + rows + tail)


def _parse(ts) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def schedule(db, business_id: str, client_id: str, bill_id: str,
             now: _dt.datetime | None = None) -> None:
    """Queue a notice for one new bill, merging with the customer's pending ones."""
    now = now or _now()
    try:
        pending = (db.table("bill_notices").select("id, first_at")
                   .eq("business_id", business_id).eq("client_id", client_id)
                   .eq("status", "pending").execute()).data or []
        firsts = [p for p in (_parse(r.get("first_at")) for r in pending) if p]
        first_at = min(firsts) if firsts else None
        last = (db.table("bill_notices").select("sent_at")
                .eq("business_id", business_id).eq("client_id", client_id)
                .eq("status", "sent").order("sent_at", desc=True).limit(1).execute()).data or []
        last_sent = _parse(last[0].get("sent_at")) if last else None
        when = next_send_time(now, first_at=first_at, last_sent_at=last_sent)
        db.table("bill_notices").insert({
            "business_id": business_id, "client_id": client_id, "bill_id": bill_id,
            "first_at": (first_at or now).isoformat(), "send_after": when.isoformat(),
        }).execute()
        if pending:
            (db.table("bill_notices").update({"send_after": when.isoformat()})
             .eq("business_id", business_id).eq("client_id", client_id)
             .eq("status", "pending").execute())
    except Exception:
        log.warning("bill notice not scheduled for bill %s", bill_id, exc_info=True)
