"""The "Today" snapshot: the one reason to open ASVA each morning.

Answers the two questions every distributor wakes up with - *did I get paid?*
and *who do I chase today?* - in a single best-effort read, so the desktop Today
screen, a future morning-brief WhatsApp, and the tests all quote the same truth.

Design rules (same discipline as proof/forecast/scorecard):
  - Honest numbers only. Every figure is derived from the shop's own bills and
    booked receipts; nothing is invented. A missing table or a failed query
    degrades that one field to zero/empty - it never raises.
  - One bills fetch feeds outstanding, the aging/DSO number, and the chase list,
    so the page stays fast even for a ~1,000-party shop.
  - Returns plain JSON-serialisable primitives (floats/ints/strings) so the
    admin endpoint can hand it straight to the browser.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

from app.services import aging, forecast, names, promises, proof, scorecard

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
CHASE_LIMIT = 8


def _today() -> _dt.date:
    return _dt.datetime.now(IST).date()


def _d(x) -> Decimal:
    try:
        return Decimal(str(x if x is not None else 0))
    except Exception:
        return Decimal(0)


def _greeting(now: _dt.datetime) -> str:
    h = now.hour
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"


def _fetch_open_bills(db, business_id: str) -> list[dict]:
    """All open bills for the shop, paged past Supabase's 1,000-row cap, each
    with its party's name + number. Best-effort -> []."""
    out: list[dict] = []
    start = 0
    page = 1000
    while True:
        try:
            resp = (db.table("bills")
                    .select("outstanding, invoice_date, due_date, client_id, "
                            "clients(name, whatsapp_number, excluded)")
                    .eq("business_id", business_id)
                    .in_("status", ["pending", "partial", "overdue"])
                    .range(start, start + page - 1).execute())
        except Exception:
            log.debug("today: bills page fetch failed (%s)", business_id, exc_info=True)
            break
        rows = resp.data or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _dial_number(raw: str | None) -> str:
    """A clean 10-digit dialling number (strip the 91 country code), or '' when
    there is none - so the chase list can offer tap-to-call."""
    t = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(t) == 12 and t.startswith("91"):
        t = t[2:]
    elif len(t) == 13 and t.startswith("091"):
        t = t[3:]
    return t if len(t) == 10 else ""


def _receipts_sum(db, business_id: str, day: _dt.date) -> tuple[float, int]:
    """(amount, count) of receipts booked in Tally on `day`. Best-effort -> (0,0)."""
    try:
        rows = (db.table("tally_receipts").select("amount")
                .eq("business_id", business_id)
                .eq("receipt_date", day.isoformat()).execute()).data or []
        return float(sum((_d(r.get("amount")) for r in rows), Decimal(0))), len(rows)
    except Exception:
        return 0.0, 0


def build_today(db, business_id: str, business: dict, *,
                today: _dt.date | None = None) -> dict:
    """The full Today snapshot for one shop."""
    now = _dt.datetime.now(IST)
    today = today or now.date()
    yesterday = today - _dt.timedelta(days=1)

    open_bills = _fetch_open_bills(db, business_id)

    # ── Outstanding + aging/DSO (one pass over the bills we already have) ──
    ag = aging.compute(open_bills, today)

    # ── Chase list: aggregate overdue by party, rank by amount x lateness ──
    per_party: dict = {}
    party_ids_with_outstanding: set = set()
    no_number_ids: set = set()
    for b in open_bills:
        out = _d(b.get("outstanding"))
        if out <= 0:
            continue
        cid = b.get("client_id")
        if not cid:
            continue
        party_ids_with_outstanding.add(cid)
        cl = b.get("clients") or {}
        # Do-not-chase (excluded) parties never appear on the chase list or in the
        # "add a number" nudge - the owner has deliberately taken them off.
        if cl.get("excluded"):
            continue
        phone = cl.get("whatsapp_number")
        if not phone:
            no_number_ids.add(cid)
        e = per_party.setdefault(cid, {
            "client_id": cid, "name": cl.get("name") or "-", "phone": phone,
            "total": Decimal(0), "days_late": 0})
        e["total"] += out
        due = b.get("due_date")
        if due:
            try:
                late = (today - _dt.date.fromisoformat(str(due)[:10])).days
                if late > e["days_late"]:
                    e["days_late"] = late
            except (TypeError, ValueError):
                pass

    # Only parties who are actually overdue belong on the chase list; rank by the
    # money-at-stake signal (amount weighted by how late it is), biggest first.
    # Rank = money at stake, gently boosted by age (~+1x per month overdue) so a
    # much older smaller bill can climb, but the biggest money still leads.
    overdue = [e for e in per_party.values() if e["days_late"] > 0]
    overdue.sort(key=lambda e: float(e["total"]) * (1 + e["days_late"] / 30.0),
                 reverse=True)
    chase = []
    for e in overdue[:CHASE_LIMIT]:
        disp = names.clean_display(e["name"] or "") or (e["name"] or "-")
        item = {
            "client_id": e["client_id"],
            "name": e["name"],
            "display": disp,
            "amount": float(e["total"]),
            "days_late": e["days_late"],
            "has_number": bool(e["phone"]),
            "phone": _dial_number(e["phone"]),   # clean 10-digit for a tap-to-call
        }
        # Reliability grade for the shown parties only (accurate: uses the party's
        # own bills + promises + receipts). Best-effort - never breaks the page.
        try:
            sc = scorecard.build_scorecard(
                db, business_id, {"id": e["client_id"], "name": e["name"]}, today=today)
            item["grade"] = sc.get("grade")
            item["grade_label"] = sc.get("grade_label")
            item["grade_color"] = sc.get("color")
        except Exception:
            item["grade"] = None
        chase.append(item)

    # ── Money in: yesterday + today (booked receipts) ─────────────────────
    y_amt, y_cnt = _receipts_sum(db, business_id, yesterday)
    t_amt, t_cnt = _receipts_sum(db, business_id, today)

    # ── Cash coming in (forecast) + recovered this/last month (proof) ─────
    try:
        f = forecast.cash_in_forecast(db, business_id)
    except Exception:
        f = {"total": Decimal(0), "promised": Decimal(0), "due_soon": Decimal(0),
             "horizon_days": 7, "promised_count": 0, "due_count": 0}
    try:
        pf = proof.build_proof(db, business_id, today)
    except Exception:
        pf = {"month": today.strftime("%B"), "recovered_this_month": Decimal(0),
              "recovered_last_month": Decimal(0), "outstanding": Decimal(0)}
    this_m = float(pf.get("recovered_this_month") or 0)
    last_m = float(pf.get("recovered_last_month") or 0)

    # ── Owe-money-but-no-number: the actual parties, so the owner can add a
    # number for each right on Today (biggest owed first). This is the honest,
    # actionable list - NOT "every party with no number".
    no_number_parties = sorted(
        ({"client_id": cid,
          "display": names.clean_display(per_party[cid]["name"] or "") or (per_party[cid]["name"] or "Customer"),
          "amount": float(per_party[cid]["total"])}
         for cid in no_number_ids if cid in per_party),
        key=lambda x: x["amount"], reverse=True)[:50]

    # ── Open promises (who said they'd pay) ───────────────────────────────
    proms = []
    try:
        rows = promises.open_for_business(db, business_id)
    except Exception:
        rows = []
    # Map client_id -> name from the bills we already loaded (avoids a fetch).
    name_by_id = {cid: e["name"] for cid, e in per_party.items()}
    for p in rows[:8]:
        cid = p.get("client_id")
        nm = name_by_id.get(cid) or "Customer"
        proms.append({
            "client_id": cid,
            "display": names.clean_display(nm) or nm,
            "kind": p.get("kind"),
            "said": (p.get("raw_text") or "")[:120],
            "promise_date": str(p.get("promise_date"))[:10] if p.get("promise_date") else None,
        })

    return {
        "greeting": _greeting(now),
        "date_label": today.strftime("%A, %d %B"),
        "business": business.get("business_name") or "Your shop",
        "money_in": {
            "yesterday": {"amount": y_amt, "count": y_cnt},
            "today": {"amount": t_amt, "count": t_cnt},
        },
        "coming_in": {
            "total": float(f.get("total") or 0),
            "promised": float(f.get("promised") or 0),
            "due_soon": float(f.get("due_soon") or 0),
            "days": f.get("horizon_days", 7),
            "promised_count": f.get("promised_count", 0),
            "due_count": f.get("due_count", 0),
        },
        "recovered": {
            "month_label": pf.get("month", today.strftime("%B")),
            "this_month": this_m,
            "last_month": last_m,
            "delta": this_m - last_m,
            "up": this_m >= last_m,
        },
        "outstanding": {
            "total": ag["total"],
            "party_count": len(party_ids_with_outstanding),
        },
        "dso": {
            "days_stuck": ag["days_stuck"],
            "buckets": ag["buckets"],
        },
        "chase": chase,
        "chase_overdue_count": len(overdue),
        "promises": {"count": len(rows), "items": proms},
        "no_number": {"count": len(no_number_ids), "parties": no_number_parties},
    }
