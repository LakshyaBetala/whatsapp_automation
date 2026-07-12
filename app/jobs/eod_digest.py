"""Daily digest - sends the owner one simple end-of-day summary.

Runs HOURLY via APScheduler; each business sends once its OWN hour
(businesses.digest_hour, owner-settable from the bot with "DIGEST 9PM";
default settings.eod_digest_hour) is reached, with per-day dedup - so a
laptop that was off at the digest hour still delivers the digest the next
hour it is on. Skips if all metrics are zero.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan, PLAN_LIMITS
from app.services import whatsapp
from app.services.templates import inr, render

log = logging.getLogger(__name__)

# IST offset - avoids pytz dependency
IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> date:
    """Current date in IST (not system-local, not UTC)."""
    return datetime.now(IST).date()


def _today_utc_range_for_ist(ist_date: date) -> tuple[str, str]:
    """Return (start_utc_iso, end_utc_iso) covering one IST calendar day.

    Used to filter ``timestamptz`` columns for "today" in IST.
    """
    start = datetime.combine(ist_date, datetime.min.time(), tzinfo=IST)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


# ======================================================================
# Core digest builder (shared by run() and preview())
# ======================================================================

async def _build_digest(business_id: str, business: dict) -> dict | None:
    """Build the 5 EOD metrics for one business.

    Returns a dict of template params, or ``None`` if all values are zero
    (no point wasting ₹0.145 on an empty digest).
    """
    db = require_db()
    today = _today_ist()
    today_iso = today.isoformat()
    utc_start, utc_end = _today_utc_range_for_ist(today)

    # ── 1. Today's bills: COUNT + SUM(amount) ─────────────────────────
    bills_resp = (
        db.table("bills")
        .select("amount")
        .eq("business_id", business_id)
        .eq("invoice_date", today_iso)
        .execute()
    )
    bills_rows = bills_resp.data or []
    bills_count = len(bills_rows)
    bills_total = sum(Decimal(str(b["amount"])) for b in bills_rows)

    # ── 2. Today's payments: unique payers + approximate value ────────
    #    Count: distinct client_ids with payment_confirmation today.
    #    Value: sum paid_amount of bills updated today with paid_amount > 0.
    #    (Approximate - a proper payments ledger would fix this in a future PR.)
    pay_msgs_resp = (
        db.table("messages")
        .select("client_id")
        .eq("business_id", business_id)
        .eq("type", "payment_confirmation")
        .gte("sent_at", utc_start)
        .lt("sent_at", utc_end)
        .execute()
    )
    payers = set(
        m["client_id"]
        for m in (pay_msgs_resp.data or [])
        if m.get("client_id")
    )
    payers_count = len(payers)

    paid_bills_resp = (
        db.table("bills")
        .select("paid_amount")
        .eq("business_id", business_id)
        .gt("paid_amount", 0)
        .gte("updated_at", utc_start)
        .lt("updated_at", utc_end)
        .execute()
    )
    payments_total = sum(
        Decimal(str(b["paid_amount"])) for b in (paid_bills_resp.data or [])
    )

    # ── 3. Total outstanding ──────────────────────────────────────────
    outstanding_resp = (
        db.table("bills")
        .select("outstanding")
        .eq("business_id", business_id)
        .in_("status", ["pending", "partial", "overdue"])
        .execute()
    )
    total_outstanding = sum(
        Decimal(str(b["outstanding"])) for b in (outstanding_resp.data or [])
    )

    # ── 4. Oldest unpaid bill ─────────────────────────────────────────
    oldest_resp = (
        db.table("bills")
        .select("outstanding, invoice_date, client_id, clients(name)")
        .eq("business_id", business_id)
        .in_("status", ["pending", "partial", "overdue"])
        .order("invoice_date", desc=False)
        .limit(1)
        .execute()
    )
    oldest = oldest_resp.data[0] if oldest_resp.data else None
    if oldest:
        oldest_name = (oldest.get("clients") or {}).get("name", "-")
        oldest_amount = Decimal(str(oldest["outstanding"]))
        oldest_date = date.fromisoformat(str(oldest["invoice_date"]))
        oldest_days = (today - oldest_date).days
    else:
        oldest_name = "-"
        oldest_amount = Decimal(0)
        oldest_days = 0

    # ── 5. Reminders sent today (informational, not in template yet) ──
    reminders_resp = (
        db.table("messages")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("type", "reminder")
        .gte("sent_at", utc_start)
        .lt("sent_at", utc_end)
        .execute()
    )
    reminders_today = reminders_resp.count or 0

    # ── Skip if everything is zero ────────────────────────────────────
    all_zero = (
        bills_count == 0
        and payers_count == 0
        and total_outstanding == 0
        and oldest_days == 0
    )
    if all_zero:
        log.info("EOD digest for %s - all zeros, skipping send", business_id)
        return None

    # ── Check stale sync ──────────────────────────────────────────────
    last_sync_resp = (
        db.table("tally_syncs")
        .select("synced_at")
        .eq("business_id", business_id)
        .order("synced_at", desc=True)
        .limit(1)
        .execute()
    )
    stale_warning = ""
    if last_sync_resp.data:
        last_synced = datetime.fromisoformat(
            last_sync_resp.data[0]["synced_at"].replace("Z", "+00:00")
        )
        if last_synced.astimezone(IST).date() != today:
            stale_warning = "\n\nNote: Tally did not sync today. Figures may be old."
    else:
        stale_warning = "\n\nNote: Tally has never synced. Please start ASVA on the shop laptop."

    # ── "Worth a call": top overdue PARTIES (aggregated), the action list ─
    action_lines = ""
    try:
        od_resp = (
            db.table("bills")
            .select("outstanding, due_date, client_id, clients(name)")
            .eq("business_id", business_id)
            .eq("status", "overdue")
            .execute()
        )
        per_party: dict = {}
        for b in od_resp.data or []:
            out = Decimal(str(b.get("outstanding") or 0))
            if out <= 0:
                continue
            e = per_party.setdefault(b["client_id"], {
                "name": (b.get("clients") or {}).get("name", "-"),
                "total": Decimal(0), "late": 0})
            e["total"] += out
            if b.get("due_date"):
                try:
                    e["late"] = max(e["late"], (today - date.fromisoformat(str(b["due_date"]))).days)
                except (TypeError, ValueError):
                    pass
        top = sorted(per_party.values(), key=lambda e: e["total"], reverse=True)[:3]
        if top:
            lines = []
            for i, e in enumerate(top, 1):
                late = f" ({e['late']} days late)" if e["late"] > 0 else ""
                lines.append(f"{i}. {e['name']}: {inr(e['total'])}{late}")
            action_lines = "\n\nWORTH A CALL\n" + "\n".join(lines)
    except Exception:
        log.exception("Worth-a-call list failed - digest continues")

    # ── Build template params ─────────────────────────────────────────
    biz_name = business.get("business_name") or "Business"

    return {
        "business": biz_name,
        "date": today.strftime("%d-%m-%Y"),
        "bills_count": str(bills_count),
        "bills_total": inr(bills_total),
        "payers_count": str(payers_count),
        "payments_total": inr(payments_total),
        "outstanding_total": inr(total_outstanding),
        "oldest_name": oldest_name,
        "oldest_amount": inr(oldest_amount),
        "oldest_days": str(oldest_days),
        "stale_warning": stale_warning,
        "reminders_today": reminders_today,
        "action_lines": action_lines,
        # Flat list for AiSensy templateParams (order must match approved template)
        "_template_params": [
            biz_name,
            today.strftime("%d-%m-%Y"),
            str(bills_count),
            inr(bills_total),
            str(payers_count),
            inr(payments_total),
            inr(total_outstanding),
            oldest_name,
            inr(oldest_amount),
            str(oldest_days),
        ],
    }


# ======================================================================
# Scheduled job
# ======================================================================

async def _digest_sent_today(db, business_id: str, today: date) -> bool:
    """Per-day dedup: has this business already received today's digest?"""
    utc_start, utc_end = _today_utc_range_for_ist(today)
    resp = (
        db.table("messages")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("type", MessageType.eod_digest.value)
        .gte("sent_at", utc_start)
        .lt("sent_at", utc_end)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


async def run() -> None:
    """Runs hourly. Sends each business its digest once its own digest_hour
    is reached today (catch-up friendly: a laptop off at the hour sends the
    digest the next hour it is on; per-day dedup stops doubles)."""
    db = require_db()
    now_ist = datetime.now(IST)
    today = now_ist.date()

    biz_resp = (
        db.table("businesses")
        .select("id, business_name, whatsapp_number, plan, eod_enabled, "
                "plan_expires_on, digest_hour")
        .eq("eod_enabled", True)
        .execute()
    )
    businesses = biz_resp.data or []

    from app.services import subscription as subs

    sent = 0
    skipped = 0

    for biz in businesses:
        try:
            dh = biz.get("digest_hour")
            dh = settings.eod_digest_hour if dh is None else int(dh)
            if now_ist.hour < dh:
                skipped += 1
                continue
            if await _digest_sent_today(db, biz["id"], today):
                skipped += 1
                continue
            sub_status = subs.effective_status(biz.get("plan_expires_on"))
            if sub_status == "suspended":
                skipped += 1
                continue
            params = await _build_digest(biz["id"], biz)
            if params is None:
                skipped += 1
                continue

            # Append stale warning to the last template param if needed
            stale_warning = params.get("stale_warning", "")

            tpl_name, rendered_body = render(
                "eod_digest",
                Lang.hi,
                business=params["business"],
                date=params["date"],
                bills_count=params["bills_count"],
                bills_total=params["bills_total"],
                payers_count=params["payers_count"],
                payments_total=params["payments_total"],
                reminders_today=params["reminders_today"],
                outstanding_total=params["outstanding_total"],
            )
            action_lines = params.get("action_lines", "")

            renewal_note = ""
            if sub_status == "grace":
                left = subs.GRACE_DAYS + (subs.days_left(biz.get("plan_expires_on")) or 0)
                renewal_note = (
                    f"\n\nYour ASVA subscription has expired. Renew within "
                    f"{left} days or reminders will stop."
                )

            await whatsapp.send_template(
                business_id=biz["id"],
                to_number=biz["whatsapp_number"],
                campaign_name=tpl_name,
                template_params=params["_template_params"],
                business_name=params["business"],
                plan=Plan(biz["plan"]),
                message_type=MessageType.eod_digest,
                language=Lang.hi,
                message_text=(rendered_body + action_lines
                              + "\n\nSend LIST to see who owes you."
                              + (stale_warning or "") + renewal_note),
                channel="platform",
            )
            sent += 1

        except Exception:
            log.exception("EOD digest failed for business %s", biz["id"])

    log.info("EOD digest complete - sent=%d, skipped=%d", sent, skipped)


# ======================================================================
# Preview (GET /eod/{business_id} - no send)
# ======================================================================

async def preview(business_id: str) -> dict:
    """Build and return tonight's digest without sending.

    Powers ``GET /eod/{business_id}`` for testing and demos.
    """
    db = require_db()
    biz_resp = (
        db.table("businesses")
        .select("id, business_name, whatsapp_number, plan, eod_enabled")
        .eq("id", business_id)
        .single()
        .execute()
    )
    if not biz_resp.data:
        return {"error": "Business not found"}

    params = await _build_digest(business_id, biz_resp.data)
    if params is None:
        return {
            "message": "All values are zero - digest would not be sent tonight.",
            "would_send": False,
        }

    _, rendered = render(
        "eod_digest",
        Lang.hi,
        business=params["business"],
        date=params["date"],
        bills_count=params["bills_count"],
        bills_total=params["bills_total"],
        payers_count=params["payers_count"],
        payments_total=params["payments_total"],
        reminders_today=params["reminders_today"],
        outstanding_total=params["outstanding_total"],
    )

    rendered += params.get("action_lines", "")
    rendered += "\n\nSend LIST to see who owes you."
    stale_warning = params.get("stale_warning", "")
    if stale_warning:
        rendered += stale_warning

    return {
        "would_send": True,
        "rendered_message": rendered,
        "data": {k: v for k, v in params.items() if not k.startswith("_")},
    }
