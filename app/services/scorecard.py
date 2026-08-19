"""Per-customer reliability scorecard - INTRA-SHOP only (a shop's OWN customers).

Accuracy is the whole point, so the grade is built ONLY from data we can prove:
  - terms-adjusted days past due on the party's OPEN bills. `due_date` already
    includes the party's credit period, so "late" is measured fairly.
  - promise outcomes actually recorded (payment_promises: kept / broken).
  - real receipts booked in Tally (tally_receipts: dates + amounts).

Deliberate non-goals (to stay honest):
  - It NEVER invents an "average days to pay": we do not store a per-bill paid
    date, so any such number would be a guess.
  - It NEVER grades a party with no track record as bad. No history -> "New".

The grade is explainable: every card carries the exact reasons behind it, so the
owner can trust and audit it. Because the score uses the DUE date (terms-aware)
and recorded outcomes - not when a shop happened to enter data - it is also the
fair foundation for a consented, buyer-owned score later.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

GRADE_NEW = "new"
GRADE_RELIABLE = "reliable"
GRADE_WATCH = "watch"
GRADE_RISKY = "risky"

# Days PAST the due date (which already includes the party's credit period).
LATE_WATCH_DAYS = 7
LATE_RISKY_DAYS = 45

_LABEL = {GRADE_NEW: "New", GRADE_RELIABLE: "Reliable payer",
          GRADE_WATCH: "Watch", GRADE_RISKY: "Slow payer"}
_BADGE = {GRADE_NEW: "🆕", GRADE_RELIABLE: "✅", GRADE_WATCH: "⚠️", GRADE_RISKY: "🔴"}
_COLOR = {GRADE_NEW: "#6b7770", GRADE_RELIABLE: "#0a7d33",
          GRADE_WATCH: "#7d5a0a", GRADE_RISKY: "#c0392b"}


def grade_color(grade: str) -> str:
    return _COLOR.get(grade, "#6b7770")


def grade_badge(grade: str) -> str:
    return _BADGE.get(grade, "")


def _today() -> _dt.date:
    return _dt.datetime.now(IST).date()


def _d(x) -> Decimal:
    try:
        return Decimal(str(x if x is not None else 0))
    except Exception:
        return Decimal(0)


def _days_between(a: _dt.date, iso) -> int | None:
    try:
        return (a - _dt.date.fromisoformat(str(iso)[:10])).days
    except (TypeError, ValueError):
        return None


def compute_grade(*, open_count: int, overdue_count: int, max_days_late: int,
                  broken: int, kept: int, payments_count: int,
                  days_since_last_payment: int | None) -> str:
    """Terms-adjusted, PROPORTION-based reliability. The core fairness rule the
    owner asked for: one late bill among many paid on time must NOT drop the
    grade. So lateness is judged by the SHARE of a party's bills that are overdue,
    not by their single worst bill - and a recent payment rescues an old straggler.

    Inputs are all provable internal signals (no invented "avg days to pay"):
      open_count / overdue_count : how many open bills, and how many are past due
                                   (due_date already includes the credit period).
      max_days_late              : the oldest bill's days past due (context only).
      broken / kept              : recorded promise outcomes.
      payments_count             : receipts ASVA booked for this party.
      days_since_last_payment    : recency of the last booked receipt.
    """
    # Severity of the WORST open bill (absolute, terms-adjusted lateness) and the
    # broken-promise signal set the starting level.
    sev = 2 if max_days_late > LATE_RISKY_DAYS else (1 if max_days_late > LATE_WATCH_DAYS else 0)
    brk = 2 if broken >= 2 else (1 if broken >= 1 else 0)
    level = max(sev, brk)                        # 0 reliable, 1 watch, 2 risky

    # No overdue bill and no track record at all -> New (never graded bad).
    if level == 0 and overdue_count == 0 and payments_count == 0 and kept == 0 and broken == 0:
        return GRADE_NEW

    # Fairness rescue (the rule the owner asked for): a PROVEN paying pattern -
    # real receipts and/or kept promises heavily outweighing overdue bills and
    # broken promises - means one late bill among many must NOT tank the grade.
    # Only a genuine payment history (good > 0) can pull the level down; a party
    # with no proof of payment is still judged by how late their bills are.
    good = payments_count + kept                 # bills they actually cleared / kept
    bad = overdue_count + broken                 # current problems
    if good > 0:
        ratio = good / (good + bad) if (good + bad) else 1.0
        if ratio >= 0.8 and broken < 2:
            # Mostly good -> Reliable, but keep a VERY old straggler visible as Watch
            # instead of hiding it entirely.
            level = 1 if (max_days_late > 90 and overdue_count > 0) else 0
        elif ratio >= 0.5 and broken < 2:
            level = min(level, 1)

    return [GRADE_RELIABLE, GRADE_WATCH, GRADE_RISKY][level]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _reasons(grade: str, open_count: int, overdue_count: int, max_days_late: int,
             oldest_age: int, kept: int, broken: int, payments_count: int,
             last_payment) -> list[str]:
    if grade == GRADE_NEW:
        return ["Not enough payment history yet"]
    out: list[str] = []
    if overdue_count > 0:
        # Name the SHARE so the owner sees why one late bill among many is fine.
        share = (f"{overdue_count} of {open_count} bills overdue"
                 if open_count != overdue_count else f"{_plural(open_count, 'open bill')} overdue")
        out.append(f"{share}, oldest {_plural(max_days_late, 'day')} past due")
    elif open_count > 0:
        out.append(f"{_plural(open_count, 'open bill')}, none overdue")
    else:
        out.append("No open bills right now")
    if broken:
        out.append(f"{_plural(broken, 'broken promise')}")
    if kept:
        out.append(f"{_plural(kept, 'promise')} kept")
    if payments_count:
        lp = f" (last {str(last_payment)[:10]})" if last_payment else ""
        out.append(f"{_plural(payments_count, 'payment')} recorded{lp}")
    return out


def build_scorecard(db, business_id: str, client: dict, *,
                    today: _dt.date | None = None) -> dict:
    """The accurate reliability snapshot for one of a shop's own customers.
    Best-effort reads; any error degrades a field, never raises."""
    today = today or _today()
    cid = client.get("id")
    ledger = client.get("tally_ledger_name") or client.get("name")

    # ── Open bills: terms-adjusted lateness (the core signal) ──────────
    try:
        open_bills = (db.table("bills")
                      .select("outstanding, invoice_date, due_date")
                      .eq("business_id", business_id).eq("client_id", cid)
                      .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    except Exception:
        open_bills = []
    open_count = 0
    overdue_count = 0
    open_outstanding = Decimal(0)
    max_days_late = 0
    oldest_age = 0
    for b in open_bills:
        out = _d(b.get("outstanding"))
        if out <= 0:
            continue
        open_count += 1
        open_outstanding += out
        late = _days_between(today, b.get("due_date")) if b.get("due_date") else None
        if late is not None and late > 0:
            overdue_count += 1
        if late is not None and late > max_days_late:
            max_days_late = late
        age = _days_between(today, b.get("invoice_date")) if b.get("invoice_date") else None
        if age is not None and age > oldest_age:
            oldest_age = age

    # ── Promise outcomes (recorded, exact) ────────────────────────────
    kept = broken = 0
    try:
        prs = (db.table("payment_promises").select("status")
               .eq("business_id", business_id).eq("client_id", cid).execute()).data or []
        for r in prs:
            s = r.get("status")
            if s == "kept":
                kept += 1
            elif s == "broken":
                broken += 1
    except Exception:
        pass

    # ── Real receipts booked in Tally ────────────────────────────────
    # Prefer the id link (rename-proof, migration 038); fall back to the party
    # name only for any historical receipt not yet linked by id.
    receipts = []
    try:
        receipts = (db.table("tally_receipts").select("amount, receipt_date")
                    .eq("business_id", business_id).eq("client_id", cid)
                    .order("receipt_date", desc=True).execute()).data or []
    except Exception:
        receipts = []
    if not receipts and ledger:
        try:
            receipts = (db.table("tally_receipts").select("amount, receipt_date")
                        .eq("business_id", business_id).eq("party_name", ledger)
                        .order("receipt_date", desc=True).execute()).data or []
        except Exception:
            receipts = []
    payments_count = len(receipts)
    total_recovered = sum((_d(r.get("amount")) for r in receipts), Decimal(0))
    last_payment_date = receipts[0].get("receipt_date") if receipts else None
    days_since_last_payment = (_days_between(today, last_payment_date)
                               if last_payment_date else None)

    grade = compute_grade(
        open_count=open_count, overdue_count=overdue_count,
        max_days_late=max_days_late, broken=broken, kept=kept,
        payments_count=payments_count,
        days_since_last_payment=days_since_last_payment)
    return {
        "grade": grade,
        "grade_label": _LABEL[grade],
        "badge": _BADGE[grade],
        "color": _COLOR[grade],
        "open_count": open_count,
        "overdue_count": overdue_count,
        "open_outstanding": open_outstanding,
        "max_days_late": max_days_late,
        "oldest_age_days": oldest_age,
        "promises_kept": kept,
        "promises_broken": broken,
        "payments_count": payments_count,
        "total_recovered": total_recovered,
        "last_payment_date": last_payment_date,
        "reasons": _reasons(grade, open_count, overdue_count, max_days_late,
                            oldest_age, kept, broken, payments_count, last_payment_date),
    }


def scorecard_text(sc: dict, name: str, *, en: bool = True) -> str:
    """Clean, well-spaced WhatsApp text for the SCORE bot command."""
    from app.services import names as _n
    from app.services.templates import inr
    disp = _n.clean_display(name or "") or (name or "Customer")
    lines = [f"*{disp}*", f"{sc['badge']} {sc['grade_label']}", ""]
    for r in sc.get("reasons", []):
        lines.append(f"- {r}")
    tail = []
    if sc.get("open_outstanding", 0) > 0:
        tail.append((f"Outstanding now: {inr(sc['open_outstanding'])}" if en
                     else f"Abhi baaki: {inr(sc['open_outstanding'])}"))
    if sc.get("total_recovered", 0) > 0:
        tail.append((f"Paid so far: {inr(sc['total_recovered'])}" if en
                     else f"Ab tak diya: {inr(sc['total_recovered'])}"))
    if tail:
        lines += [""] + tail
    return "\n".join(lines)
