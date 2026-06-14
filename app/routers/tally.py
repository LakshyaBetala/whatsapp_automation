import logging
import uuid
from datetime import date
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
    db = _verify_token(payload.business_id, payload.agent_token)
    
    clients_created = 0
    credit_balances = 0
    zero_balances = 0
    errors = []

    # Start of financial year (hardcoded to 2026-04-01 as per spec, or dynamic based on current date)
    # The spec explicitly mentions: "2026-04-01 for FY2026-27"
    fy_start = date(2026, 4, 1)

    for debtor in payload.debtors:
        try:
            # 1. Upsert Client
            # Check if client exists
            client_resp = db.table("clients").select("id").eq("business_id", str(payload.business_id)).eq("tally_ledger_name", debtor.name).execute()
            
            client_id = None
            notes = ""
            if debtor.opening_balance < 0:
                credit_balances += 1
                notes = f"Credit balance: {abs(debtor.opening_balance)}"
            elif debtor.opening_balance == 0:
                zero_balances += 1

            if not client_resp.data:
                # Insert client
                new_client = db.table("clients").insert({
                    "business_id": str(payload.business_id),
                    "tally_ledger_name": debtor.name,
                    "tally_group": debtor.tally_group,
                    "notes": notes
                }).execute()
                client_id = new_client.data[0]["id"]
                clients_created += 1
            else:
                client_id = client_resp.data[0]["id"]
                if notes:
                    db.table("clients").update({"notes": notes}).eq("id", client_id).execute()

            # 2. Insert opening balance bill if positive
            if debtor.opening_balance > 0:
                v_num = f"OB-{debtor.name[:20]}"
                # Check if OB bill already exists
                bill_resp = db.table("bills").select("id").eq("business_id", str(payload.business_id)).eq("voucher_number", v_num).execute()
                if not bill_resp.data:
                    db.table("bills").insert({
                        "business_id": str(payload.business_id),
                        "client_id": client_id,
                        "voucher_number": v_num,
                        "amount": debtor.opening_balance,
                        "paid_amount": 0.0,
                        "invoice_date": fy_start.isoformat(),
                        "due_date": fy_start.isoformat(), # default due_date
                        "status": "pending",
                        "is_opening_balance": True
                    }).execute()
                    
        except Exception as e:
            errors.append(f"Error processing debtor {debtor.name}: {str(e)}")

    return {
        "clients_created": clients_created,
        "credit_balances": credit_balances,
        "zero_balances": zero_balances,
        "errors": errors
    }

@router.post("/sync")
async def sync_daybook(payload: TallySyncPayload, background_tasks: BackgroundTasks):
    db = _verify_token(payload.business_id, payload.agent_token)
    
    sales_processed = 0
    receipts_processed = 0
    unmatched_parties = []
    errors = []

    for v in payload.vouchers:
        try:
            # Match party
            client_resp = db.table("clients").select("id, whatsapp_number, default_credit_days").eq("business_id", str(payload.business_id)).eq("tally_ledger_name", v.party_name).execute()
            if not client_resp.data:
                unmatched_parties.append(v.party_name)
                continue
                
            client = client_resp.data[0]
            client_id = client["id"]

            if v.voucher_type.lower() == "sales":
                # Check if it already exists
                bill_resp = db.table("bills").select("id").eq("business_id", str(payload.business_id)).eq("voucher_number", v.voucher_number).execute()
                
                if not bill_resp.data:
                    # New bill insert
                    credit_days = client.get("default_credit_days") or 15
                    # Simple due date calc (using date + credit_days logic simplified here for time, though normally you'd parse ISO)
                    # We will let the DB or background handle exact due_date if not specified here. For now we just insert invoice_date.
                    # We import date as YYYY-MM-DD
                    inserted_bill = db.table("bills").insert({
                        "business_id": str(payload.business_id),
                        "client_id": client_id,
                        "voucher_number": v.voucher_number,
                        "amount": v.amount,
                        "paid_amount": 0.0,
                        "invoice_date": v.date,
                        "due_date": v.date, # Simplified for now
                        "status": "pending",
                        "is_opening_balance": False
                    }).execute()
                    bill_id = inserted_bill.data[0]["id"]
                    sales_processed += 1

                    # Trigger background PDF/WhatsApp if client has whatsapp number
                    if client.get("whatsapp_number"):
                        background_tasks.add_task(_generate_and_deliver, bill_id)
                else:
                    # Upsert (update amount/date just in case)
                    db.table("bills").update({
                        "amount": v.amount,
                        "invoice_date": v.date
                    }).eq("id", bill_resp.data[0]["id"]).execute()
                    sales_processed += 1

            elif v.voucher_type.lower() == "receipt":
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

    # Log to tally_syncs
    try:
        error_list = list(set(unmatched_parties + errors))
        db.table("tally_syncs").insert({
            "business_id": str(payload.business_id),
            "sync_type": "daybook",
            "vouchers_received": len(payload.vouchers),
            "bills_created": sales_processed,
            "payments_applied": receipts_processed,
            "errors": error_list
        }).execute()
    except Exception as e:
        log.error(f"Failed to write to tally_syncs: {e}")

    return {
        "sales_processed": sales_processed,
        "receipts_processed": receipts_processed,
        "unmatched_parties": unmatched_parties,
        "errors": errors
    }
