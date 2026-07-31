"""Payment-entry queue: capture a confirmed payment, hold it, and later write it
into Tally as a Receipt once the owner confirms in the app (with Tally open).

Two responsibilities, kept separate so each is testable:
  1. allocate_fifo() - pure money math: split a payment across a party's open
     bills, oldest first, with any surplus flagged as on-account (advance).
  2. the queue (create/list/mark) - a small store of pending receipts that the
     desktop popup shows for confirm/edit before the agent posts them.

This is generic: the party ledger, the deposit ledger (Cash or any of the shop's
banks) and the bills all come from the specific shop - nothing here is hardcoded
to one business.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal, ROUND_HALF_UP

log = logging.getLogger(__name__)

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def money(x) -> Decimal:
    """A rupee amount, 2 dp, never None."""
    try:
        return Decimal(str(x if x is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def allocate_fifo(open_bills: list[dict], amount) -> tuple[list[dict], Decimal]:
    """Split `amount` across `open_bills` (oldest first), filling each bill up to
    its outstanding before moving on.

    open_bills: [{"ref": str, "outstanding": number}, ...] already ordered oldest
    first. Returns (allocations, on_account):
      allocations = [{"ref", "amount"}]  - what clears which bill (positive only)
      on_account  = leftover when the payment exceeds total outstanding (advance)

    Never over-allocates a bill, skips non-positive/zero bills, and is exact to
    the paisa. A payment with no open bills to clear returns ([], full amount).
    """
    remaining = money(amount)
    allocations: list[dict] = []
    for b in open_bills:
        if remaining <= 0:
            break
        out = money(b.get("outstanding"))
        if out <= 0:
            continue
        take = min(remaining, out)
        if take <= 0:
            continue
        allocations.append({"ref": str(b.get("ref") or ""), "amount": take})
        remaining -= take
    return allocations, remaining


# ── the pending queue ────────────────────────────────────────────────────────
def create_pending(db, business_id: str, *, client_id: str | None, party_ledger: str,
                   party_display: str, amount, deposit_ledger: str = "CASH",
                   receipt_date: str | None = None) -> dict | None:
    """Queue a receipt for the owner to confirm in the app. Amount must be
    positive. receipt_date defaults to today (IST), stored as YYYY-MM-DD."""
    amt = money(amount)
    if amt <= 0:
        raise ValueError("amount must be positive")
    row = {
        "business_id": business_id,
        "client_id": client_id,
        "party_ledger": party_ledger,
        "party_display": party_display or party_ledger,
        "amount": float(amt),
        "deposit_ledger": deposit_ledger or "CASH",
        "receipt_date": receipt_date or _dt.datetime.now(IST).date().isoformat(),
        "status": "pending",
    }
    try:
        r = db.table("pending_receipts").insert(row).execute()
        return (r.data or [row])[0]
    except Exception:
        log.exception("could not queue pending receipt (business %s)", business_id)
        return None


def list_pending(db, business_id: str) -> list[dict]:
    """Open pending receipts for this shop, oldest first (the popup queue)."""
    try:
        r = (db.table("pending_receipts")
             .select("*").eq("business_id", business_id).eq("status", "pending")
             .order("created_at").execute())
        return r.data or []
    except Exception:
        log.debug("list_pending failed (business %s)", business_id, exc_info=True)
        return []


def mark(db, pending_id: str, status: str, *, voucher_id: str | None = None,
         error: str | None = None, allocation: list | None = None) -> bool:
    """Move a pending receipt to posted / failed / skipped. On 'posted' we keep
    the Tally voucher id (and the exact FIFO allocation) so a wrong entry can be
    reverted and audited."""
    patch: dict = {"status": status}
    if status == "posted":
        patch["posted_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        if voucher_id:
            patch["tally_voucher_id"] = voucher_id
        if allocation is not None:
            patch["allocation"] = allocation
    if error is not None:
        patch["error"] = error[:500]
    try:
        db.table("pending_receipts").update(patch).eq("id", pending_id).execute()
        return True
    except Exception:
        log.exception("could not mark pending receipt %s -> %s", pending_id, status)
        return False


def confirm(db, business_id: str, pending_id: str, *, amount=None,
            deposit_ledger: str | None = None, receipt_date: str | None = None) -> dict | None:
    """The owner approved this receipt in the confirm popup (optionally editing
    the amount / deposit account / date). Move pending -> confirmed so the agent
    picks it up and posts it to Tally. Scoped by business_id (tenant isolation)."""
    patch: dict = {"status": "confirmed",
                   "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    if amount is not None:
        amt = money(amount)
        if amt <= 0:
            raise ValueError("amount must be positive")
        patch["amount"] = float(amt)
    if deposit_ledger:
        patch["deposit_ledger"] = deposit_ledger
    if receipt_date:
        patch["receipt_date"] = str(receipt_date)[:10]
    try:
        r = (db.table("pending_receipts").update(patch)
             .eq("id", pending_id).eq("business_id", business_id)
             .eq("status", "pending").execute())
        return (r.data or [None])[0]
    except Exception:
        log.exception("could not confirm pending receipt %s", pending_id)
        return None


def list_for_owner(db, business_id: str) -> list[dict]:
    """Every receipt the owner should see in the Payments tab: awaiting confirm,
    in-flight, or recently failed - so nothing silently disappears. Newest first,
    posted/skipped older than a short window are dropped by the caller if wanted."""
    try:
        r = (db.table("pending_receipts").select("*")
             .eq("business_id", business_id)
             .in_("status", ["pending", "confirmed", "failed"])
             .order("created_at", desc=True).execute())
        return r.data or []
    except Exception:
        log.debug("list_for_owner failed (business %s)", business_id, exc_info=True)
        return []


def list_confirmed(db, business_id: str) -> list[dict]:
    """Owner-approved receipts the agent must post to Tally, oldest first."""
    try:
        r = (db.table("pending_receipts").select("*")
             .eq("business_id", business_id).eq("status", "confirmed")
             .order("confirmed_at").execute())
        return r.data or []
    except Exception:
        log.debug("list_confirmed failed (business %s)", business_id, exc_info=True)
        return []


def get_by_id(db, business_id: str, pending_id: str) -> dict | None:
    try:
        r = (db.table("pending_receipts").select("*")
             .eq("id", pending_id).eq("business_id", business_id)
             .limit(1).execute())
        return (r.data or [None])[0]
    except Exception:
        return None


def set_deposit_ledgers(db, business_id: str, ledgers: list[str]) -> bool:
    """Cache the shop's Cash/Bank ledger names (from the agent) for the popup."""
    clean = [str(x).strip() for x in (ledgers or []) if str(x).strip()][:40]
    try:
        db.table("businesses").update({
            "tally_deposit_ledgers": clean,
            "tally_deposit_ledgers_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }).eq("id", business_id).execute()
        return True
    except Exception:
        log.exception("could not store deposit ledgers (business %s)", business_id)
        return False


def get_deposit_ledgers(db, business_id: str) -> list[str]:
    """The shop's cached deposit accounts; always includes 'Cash' as a fallback."""
    out: list[str] = []
    try:
        r = (db.table("businesses").select("tally_deposit_ledgers")
             .eq("id", business_id).limit(1).execute())
        raw = (r.data or [{}])[0].get("tally_deposit_ledgers") or []
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        out = [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        out = []
    if not any(x.strip().lower() == "cash" for x in out):
        out = ["Cash"] + out
    return out
