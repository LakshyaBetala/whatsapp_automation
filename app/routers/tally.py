import logging
import uuid
from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from app.db import require_db
from app.routers.bills import _generate_and_deliver

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tally", tags=["tally"])

class TallyDebtor(BaseModel):
    name: str
    opening_balance: float
    tally_group: str = ""
    whatsapp_number: Optional[str] = None  # agent extracts from Tally ledger/address
    credit_days: Optional[int] = None      # Tally BillCreditPeriod or shop default


def _normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Normalise to '91XXXXXXXXXX' or None if not a valid Indian mobile."""
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits
    return None

class TallyImportPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    company_name: str
    debtors: list[TallyDebtor]

class TallyVoucher(BaseModel):
    voucher_number: str
    voucher_type: str  # Sales or Receipt
    party_name: str
    amount: float
    date: str  # YYYY-MM-DD

class TallySyncPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    company_name: str
    sync_date: str  # YYYY-MM-DD
    vouchers: list[TallyVoucher]

def _fy_start(today: Optional[date] = None) -> date:
    """April 1 of the current Indian financial year."""
    d = today or date.today()
    year = d.year if d.month >= 4 else d.year - 1
    return date(year, 4, 1)


def _fetch_all(query_fn, page_size: int = 1000) -> list:
    """Page through a PostgREST query (Supabase caps responses at ~1000 rows).

    query_fn() must return a fresh query builder each call (filters applied,
    no range) — we add .range() per page.
    """
    rows: list = []
    start = 0
    while True:
        resp = query_fn().range(start, start + page_size - 1).execute()
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def _chunked(items: list, size: int = 200):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _verify_token(business_id: uuid.UUID, agent_token: str):
    db = require_db()
    resp = db.table("businesses").select("agent_token").eq("id", str(business_id)).execute()
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Business not found")
    if resp.data[0].get("agent_token") != agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent_token")
    return db

@router.post("/import")
async def import_outstanding(payload: TallyImportPayload):
    """Bulk import of debtors. Batched: ~6 Supabase round-trips for a
    1,000-debtor shop instead of ~2,000 (Tokyo latency made the per-row
    version time out the agent)."""
    db = _verify_token(payload.business_id, payload.agent_token)
    biz = str(payload.business_id)

    clients_created = 0
    credit_balances = 0
    zero_balances = 0
    phones_added = 0
    errors = []

    # Start of the current Indian financial year (Apr 1)
    fy_start = _fy_start()

    # ── Prefetch existing state (2 paged queries) ─────────────────────
    existing_clients = {
        c["tally_ledger_name"]: c
        for c in _fetch_all(lambda: db.table("clients")
                            .select("id, tally_ledger_name, whatsapp_number, credit_days")
                            .eq("business_id", biz))
        if c.get("tally_ledger_name")
    }
    existing_obs = {
        b["tally_voucher_number"]
        for b in _fetch_all(lambda: db.table("bills")
                            .select("tally_voucher_number")
                            .eq("business_id", biz)
                            .eq("is_opening_balance", True))
    }

    # ── Classify debtors; per-row updates only for real backfills ─────
    new_rows = []
    ledger_to_id = {name: c["id"] for name, c in existing_clients.items()}
    for debtor in payload.debtors:
        try:
            phone = _normalize_phone(debtor.whatsapp_number)
            credit_days = debtor.credit_days if debtor.credit_days and 1 <= debtor.credit_days <= 365 else None
            if debtor.opening_balance < 0:
                credit_balances += 1
            elif debtor.opening_balance == 0:
                zero_balances += 1

            existing = existing_clients.get(debtor.name)
            if existing is None:
                row = {
                    "business_id": biz,
                    "name": debtor.name,
                    "tally_ledger_name": debtor.name,
                    "tally_group": debtor.tally_group,
                    "whatsapp_number": phone,
                }
                if credit_days:
                    row["credit_days"] = credit_days
                new_rows.append(row)
                if phone:
                    phones_added += 1
            else:
                updates = {}
                # Backfill phone if Tally has one and we don't (never
                # overwrite a manually-set number)
                if phone and not existing.get("whatsapp_number"):
                    updates["whatsapp_number"] = phone
                    phones_added += 1
                # Adopt Tally credit terms only while the client still has
                # the untouched default (30) — manual edits win
                if credit_days and existing.get("credit_days", 30) == 30 and credit_days != 30:
                    updates["credit_days"] = credit_days
                if updates:
                    db.table("clients").update(updates).eq("id", existing["id"]).execute()
        except Exception as e:
            errors.append(f"Error processing debtor {debtor.name}: {str(e)}")

    # ── Bulk insert new clients (chunked) ─────────────────────────────
    for chunk in _chunked(new_rows):
        try:
            resp = db.table("clients").insert(chunk).execute()
            for c in resp.data or []:
                ledger_to_id[c["tally_ledger_name"]] = c["id"]
            clients_created += len(resp.data or [])
        except Exception as e:
            errors.append(f"Bulk client insert failed for {len(chunk)} rows: {str(e)}")

    # ── Bulk insert opening-balance bills (chunked) ───────────────────
    ob_rows = []
    payload_claimed: set = set()
    for debtor in payload.debtors:
        if debtor.opening_balance <= 0:
            continue
        client_id = ledger_to_id.get(debtor.name)
        if not client_id:
            continue
        v_num = f"OB-{debtor.name[:20]}"
        if v_num in payload_claimed:
            # Two debtors share the first 20 chars — full name disambiguates
            v_num = f"OB-{debtor.name}"
        payload_claimed.add(v_num)
        if v_num in existing_obs:
            continue
        existing_obs.add(v_num)
        ob_rows.append({
            "business_id": biz,
            "client_id": client_id,
            "invoice_number": v_num,
            "tally_voucher_number": v_num,
            "amount": debtor.opening_balance,
            "paid_amount": 0.0,
            "invoice_date": fy_start.isoformat(),
            "due_date": fy_start.isoformat(),  # already outstanding — due immediately
            "status": "pending",
            "is_opening_balance": True,
        })
    for chunk in _chunked(ob_rows):
        try:
            db.table("bills").insert(chunk).execute()
        except Exception as e:
            errors.append(f"Bulk OB-bill insert failed for {len(chunk)} rows: {str(e)}")

    return {
        "clients_created": clients_created,
        "credit_balances": credit_balances,
        "zero_balances": zero_balances,
        "phones_added": phones_added,
        "errors": errors
    }

@router.post("/sync")
async def sync_daybook(payload: TallySyncPayload, background_tasks: BackgroundTasks):
    """Apply the FY voucher dump. Batched prefetch (3 paged queries)
    instead of 2 lookups per voucher — Tokyo latency budget."""
    db = _verify_token(payload.business_id, payload.agent_token)
    biz = str(payload.business_id)

    sales_processed = 0
    new_bills = 0
    receipts_processed = 0
    unmatched_parties = []
    errors = []

    # ── Prefetch: clients, existing bills, applied receipts ──────────
    clients_by_ledger = {
        c["tally_ledger_name"]: c
        for c in _fetch_all(lambda: db.table("clients")
                            .select("id, tally_ledger_name, whatsapp_number, credit_days")
                            .eq("business_id", biz))
        if c.get("tally_ledger_name")
    }
    bills_by_voucher = {
        b["tally_voucher_number"]: b
        for b in _fetch_all(lambda: db.table("bills")
                            .select("id, tally_voucher_number, amount")
                            .eq("business_id", biz))
        if b.get("tally_voucher_number")
    }
    applied_receipts = {
        (r["tally_voucher_number"], str(r["receipt_date"]))
        for r in _fetch_all(lambda: db.table("tally_receipts")
                            .select("tally_voucher_number, receipt_date")
                            .eq("business_id", biz))
    }

    for v in payload.vouchers:
        try:
            # Match party (prefetched)
            client = clients_by_ledger.get(v.party_name)
            if not client:
                unmatched_parties.append(v.party_name)
                continue
            client_id = client["id"]

            if v.voucher_type.lower() == "sales":
                existing_bill = bills_by_voucher.get(v.voucher_number)
                if not existing_bill:
                    # New bill insert — due_date = invoice_date + client credit period
                    credit_days = client.get("credit_days") or 30
                    invoice_date = date.fromisoformat(v.date)
                    due_date = invoice_date + timedelta(days=credit_days)
                    inserted_bill = db.table("bills").insert({
                        "business_id": biz,
                        "client_id": client_id,
                        "invoice_number": v.voucher_number,
                        "tally_voucher_number": v.voucher_number,
                        "amount": v.amount,
                        "paid_amount": 0.0,
                        "invoice_date": invoice_date.isoformat(),
                        "due_date": due_date.isoformat(),
                        "status": "pending",
                        "is_opening_balance": False
                    }).execute()
                    bill_row = inserted_bill.data[0]
                    bills_by_voucher[v.voucher_number] = bill_row
                    sales_processed += 1
                    new_bills += 1

                    # Instant PDF+WhatsApp delivery ONLY for fresh bills.
                    # The first sync replays the whole FY — blasting
                    # months-old invoices at onboarding would be a
                    # disaster. Old unpaid bills enter the reminder
                    # cadence instead (drip-fed by the daily sweep).
                    if client.get("whatsapp_number") and invoice_date >= date.today() - timedelta(days=1):
                        background_tasks.add_task(_generate_and_deliver, bill_row["id"])
                else:
                    # Update only when the amount actually changed (rare)
                    if float(existing_bill.get("amount") or 0) != float(v.amount):
                        db.table("bills").update({
                            "amount": v.amount,
                            "invoice_date": v.date
                        }).eq("id", existing_bill["id"]).execute()
                    sales_processed += 1

            elif v.voucher_type.lower() == "receipt":
                # Idempotency: every sync sends the full FY (Tally ignores
                # date filters over HTTP) — apply each receipt exactly once.
                if (v.voucher_number, v.date) in applied_receipts:
                    continue
                applied_receipts.add((v.voucher_number, v.date))
                db.table("tally_receipts").insert({
                    "business_id": biz,
                    "tally_voucher_number": v.voucher_number,
                    "party_name": v.party_name,
                    "amount": v.amount,
                    "receipt_date": v.date,
                }).execute()

                # Find oldest open bills
                open_bills_resp = db.table("bills").select("id, amount, paid_amount, status").eq("client_id", client_id).in_("status", ["pending", "partial", "overdue"]).order("invoice_date").execute()

                remaining_payment = v.amount
                for b in open_bills_resp.data:
                    if remaining_payment <= 0:
                        break

                    bill_due = b["amount"] - b["paid_amount"]
                    if bill_due <= 0:
                        continue

                    pay_amt = min(remaining_payment, bill_due)
                    new_paid = b["paid_amount"] + pay_amt
                    new_status = "paid" if new_paid >= b["amount"] else "partial"

                    db.table("bills").update({
                        "paid_amount": new_paid,
                        "status": new_status
                    }).eq("id", b["id"]).execute()

                    remaining_payment -= pay_amt

                receipts_processed += 1

        except Exception as e:
            errors.append(f"Error processing {v.voucher_type} {v.voucher_number}: {str(e)}")

    # Log to tally_syncs (schema: sync_type enum, records_synced, success, error)
    try:
        # Unmatched parties (CASH, internal accounts) are informational —
        # only real errors mark the sync as failed.
        error_list = sorted(set(unmatched_parties)) + errors
        db.table("tally_syncs").insert({
            "business_id": str(payload.business_id),
            "sync_type": "poll",
            "records_synced": sales_processed + receipts_processed,
            "success": len(errors) == 0,
            "error": "; ".join(error_list)[:2000] if error_list else None,
        }).execute()
    except Exception as e:
        log.error(f"Failed to write to tally_syncs: {e}")

    return {
        "sales_processed": sales_processed,
        "new_bills": new_bills,
        "receipts_processed": receipts_processed,
        "unmatched_parties": unmatched_parties,
        "errors": errors
    }
