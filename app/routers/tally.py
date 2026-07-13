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
    pdf_base64: Optional[str] = None   # Tally's own exported invoice PDF (agent attaches for new bills)

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
    no range) - we add .range() per page.
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


def _sync_company_name(db, business_id, company_name: str) -> None:
    """The company name on the dashboard comes FROM TALLY, never hard-coded:
    whatever name the agent reports is written through to the business row.
    Best-effort - a rename must never fail a sync."""
    name = (company_name or "").strip()
    if not name:
        return
    try:
        r = (db.table("businesses").select("business_name")
             .eq("id", str(business_id)).limit(1).execute())
        if r.data and (r.data[0].get("business_name") or "") != name:
            db.table("businesses").update(
                {"business_name": name, "tally_company_name": name}
            ).eq("id", str(business_id)).execute()
            log.info("Business %s renamed from Tally: %s", business_id, name)
    except Exception:
        log.exception("Company-name sync failed (continuing)")


def _verify_token(business_id: uuid.UUID, agent_token: str):
    db = require_db()
    resp = db.table("businesses").select("agent_token").eq("id", str(business_id)).execute()
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Business not found")
    if resp.data[0].get("agent_token") != agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent_token")
    return db

@router.get("/pending-refresh")
async def pending_refresh(business_id: uuid.UUID, agent_token: str):
    """Agent polls this each watch tick. Returns whether the owner pressed
    'Reload data' (an override that forces an immediate outstanding refresh
    instead of waiting for the 5-min auto cycle). Cleared by /outstandings."""
    db = _verify_token(business_id, agent_token)
    try:
        r = (db.table("businesses").select("refresh_requested_at")
             .eq("id", str(business_id)).limit(1).execute())
        req = bool(r.data and r.data[0].get("refresh_requested_at"))
    except Exception:
        req = False  # column missing (migration 015 not applied) - non-fatal
    return {"requested": req}


class RegisterCompanyPayload(BaseModel):
    account_token: str      # agent_token of the customer's PRIMARY company
    company_name: str       # the Tally company to add


@router.post("/companies/register")
async def register_company(payload: RegisterCompanyPayload):
    """Add another Tally company under an existing customer account.

    Each Tally company gets its OWN businesses row = its own fully isolated
    data (bills/clients/messages scoped by business_id). Owner contact, plan
    and payment settings are inherited from the primary company; the bot and
    digest answer the owner from the OLDEST (primary) company. Idempotent:
    re-registering the same company returns its existing credentials.
    """
    import secrets
    db = require_db()
    acct = (db.table("businesses").select(
        "id, owner_name, business_name, whatsapp_number, plan, msg_language, "
        "upi_vpa, upi_vpa_2, upi_vpa_3, discount_pct, reminder_hour, "
        "plan_expires_on, timezone")
        .eq("agent_token", payload.account_token).order("created_at")
        .limit(1).execute())
    if not acct.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid account token")
    src = acct.data[0]
    name = (payload.company_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="company_name required")
    if name == (src.get("business_name") or ""):
        raise HTTPException(status_code=400,
                            detail="That is already the primary company")

    existing = (db.table("businesses").select("id, agent_token")
                .eq("whatsapp_number", src["whatsapp_number"])
                .eq("business_name", name).limit(1).execute())
    if existing.data:
        return {"business_id": existing.data[0]["id"],
                "agent_token": existing.data[0]["agent_token"],
                "company_name": name, "created": False}

    new_token = secrets.token_urlsafe(32)
    row = db.table("businesses").insert({
        "owner_name": src.get("owner_name") or name,
        "business_name": name,
        "tally_company_name": name,
        "whatsapp_number": src["whatsapp_number"],
        "plan": src.get("plan") or "starter",
        "msg_language": src.get("msg_language"),
        "upi_vpa": src.get("upi_vpa"),
        "upi_vpa_2": src.get("upi_vpa_2"),
        "upi_vpa_3": src.get("upi_vpa_3"),
        "discount_pct": src.get("discount_pct"),
        "reminder_hour": src.get("reminder_hour"),
        "plan_expires_on": src.get("plan_expires_on"),
        "timezone": src.get("timezone") or "Asia/Kolkata",
        "agent_token": new_token,
        "onboarding_status": "active",
    }).execute()
    log.info("Registered sibling company '%s' under %s", name, src["id"])
    return {"business_id": row.data[0]["id"], "agent_token": new_token,
            "company_name": name, "created": True}


@router.post("/import")
async def import_outstanding(payload: TallyImportPayload):
    """Bulk import of debtors. Batched: ~6 Supabase round-trips for a
    1,000-debtor shop instead of ~2,000 (Tokyo latency made the per-row
    version time out the agent)."""
    db = _verify_token(payload.business_id, payload.agent_token)
    biz = str(payload.business_id)
    _sync_company_name(db, biz, payload.company_name)

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
                    # A recovery tool exists to chase debtors, so new imports
                    # default to reminders ON. The daily cap + pacing prevent a
                    # day-one blast; the owner can pause any party.
                    "reminders_enabled": True,
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
                # the untouched default (30) - manual edits win
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
            # Two debtors share the first 20 chars - full name disambiguates
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
            "due_date": fy_start.isoformat(),  # already outstanding - due immediately
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
    instead of 2 lookups per voucher - Tokyo latency budget."""
    db = _verify_token(payload.business_id, payload.agent_token)
    biz = str(payload.business_id)
    _sync_company_name(db, biz, payload.company_name)
    # Liveness for the ops health monitor: the watcher posts here every ~60s.
    from app.services import license as _lic
    _lic.stamp_last_seen(db, biz)

    sales_processed = 0
    new_bills = 0
    receipts_processed = 0
    unmatched_parties = []
    errors = []
    delivered = []   # voucher numbers actually sent - the agent cleans these PDFs up

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
                            .select("id, tally_voucher_number, amount, pdf_url")
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
                invoice_date = date.fromisoformat(v.date)
                if not existing_bill:
                    # New bill insert - due_date = invoice_date + client credit period
                    credit_days = client.get("credit_days") or 30
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
                else:
                    bill_row = existing_bill
                    # Update only when the amount actually changed (rare)
                    if float(existing_bill.get("amount") or 0) != float(v.amount):
                        db.table("bills").update({
                            "amount": v.amount,
                            "invoice_date": v.date
                        }).eq("id", existing_bill["id"]).execute()
                    sales_processed += 1

                # Deliver to the customer ONLY when the owner exported this bill
                # from Tally: their TDL drops the exact Tally PDF into the pickup
                # folder, the agent attaches it as pdf_base64, and we send. No
                # exported PDF => the bill is recorded but NOTHING is sent, so the
                # owner controls exactly which bills go out. pdf_url is the
                # "already delivered" marker (set just before send), so a bill is
                # never re-sent even across the outstanding-reconcile. The first
                # sync replays the whole FY, so skip anything older than a few
                # days to avoid blasting historic invoices at onboarding.
                # Deliver if the bill is recent. 10-day window (was 3) so a bill
                # exported while the watcher was down/offline still goes out when
                # it recovers - but old FY invoices at first onboarding don't.
                # pdf_url doubles as the "already delivered" marker.
                already_sent = bool(bill_row.get("pdf_url"))
                fresh = invoice_date >= date.today() - timedelta(days=10)
                if v.pdf_base64 and not already_sent and fresh and client.get("whatsapp_number"):
                    # Upload to Storage for the dashboard link (best-effort), but
                    # DELIVER using the base64 we already hold - so a storage
                    # hiccup can never stop the send. Mark delivered BEFORE the
                    # background send so a slow send can't double-fire next tick.
                    url = None
                    try:
                        from app.services import pdf as pdf_service
                        url = await pdf_service.upload_pdf_base64(
                            bill_row["id"], v.voucher_number, v.pdf_base64)
                    except Exception as e:
                        log.warning("Tally PDF storage upload failed for %s (delivering anyway): %s",
                                    v.voucher_number, e)
                    marker = url or "sent"
                    db.table("bills").update({"pdf_url": marker}).eq("id", bill_row["id"]).execute()
                    bill_row["pdf_url"] = marker
                    background_tasks.add_task(
                        _generate_and_deliver, bill_row["id"], v.pdf_base64,
                        f"Invoice_{v.voucher_number}.pdf")
                    delivered.append(v.voucher_number)

            elif v.voucher_type.lower() == "receipt":
                # Idempotency: every sync sends the full FY (Tally ignores
                # date filters over HTTP) - apply each receipt exactly once.
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

                # Find oldest open TALLY bills. WhatsApp-made bills (source
                # photo/manual) are excluded: a Tally receipt is money Tally
                # recorded against Tally bills - letting it pay off a WhatsApp
                # bill would corrupt both balances. Those are settled via the
                # dashboard's record-payment or the owner's PAID command.
                open_bills_resp = db.table("bills").select("id, amount, paid_amount, status").eq("client_id", client_id).eq("source", "tally").in_("status", ["pending", "partial", "overdue"]).order("invoice_date").execute()

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
        # Unmatched parties (CASH, internal accounts) are informational -
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
        "delivered": delivered,
        "errors": errors
    }


class TallyOpenBill(BaseModel):
    party_name: str
    bill_ref: str = ""
    bill_date: Optional[str] = None   # YYYY-MM-DD
    due_date: Optional[str] = None    # YYYY-MM-DD
    amount: float                     # Tally's NET outstanding for this bill


class TallyOutstandingsPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    company_name: str
    bills: list[TallyOpenBill]
    all_parties: list[str] = []       # every debtor ledger (to clear fully-paid ones)
    # Ledger ClosingBalance per party (Tally's authoritative "what they owe
    # today" total). This is the source of truth for the amount; the bill-wise
    # list above only breaks it into aged bills WHEN it reconciles. Sending
    # this fixes parties whose ledgers don't 'maintain balances bill-by-bill'
    # (they return zero bills, so without this they'd wrongly show as cleared).
    ledger_balances: dict[str, float] = {}


async def _send_payment_confirmation(business_id, plan_name, client, delta, remaining):
    """Background: tell a customer 'received Rs X, Rs Y remaining' when Tally
    shows their balance dropped (i.e. a payment was recorded)."""
    from app.services import whatsapp
    from app.services.templates import render, inr
    from app.models import Lang, MessageType, Plan
    try:
        lang = Lang(client.get("language") or "hi")
    except Exception:
        lang = Lang.hi
    try:
        _, body = render(
            "payment_confirmation", lang,
            client=client.get("name", "Customer"),
            paid_amount=inr(delta), outstanding=inr(remaining))
        await whatsapp.send_message(
            business_id=business_id, to_number=client["whatsapp_number"],
            message_text=body, plan=Plan(plan_name),
            message_type=MessageType.payment_confirmation,
            client_id=client["id"], language=lang, channel="shop")
    except Exception:
        log.exception("payment confirmation send failed for %s", client.get("name"))


@router.post("/outstandings")
async def import_outstandings(payload: TallyOutstandingsPayload, background_tasks: BackgroundTasks):
    """Make Tally's bill-by-bill OUTSTANDING the source of truth.

    Each open bill is upserted (keyed by tally_voucher_number = TB-<client>-<ref>)
    with Tally's NET amount and real dates, so overdue days and amounts are
    exact. Bills a debtor no longer owes are marked paid. When a bill's net
    DROPS between refreshes a payment happened, so the customer gets a
    'received X, remaining Y' confirmation. Runs every sync cycle.
    """
    from collections import defaultdict
    db = _verify_token(payload.business_id, payload.agent_token)
    biz = str(payload.business_id)
    _sync_company_name(db, biz, payload.company_name)
    fy = _fy_start().isoformat()

    biz_row = db.table("businesses").select("plan").eq("id", biz).limit(1).execute()
    plan_name = biz_row.data[0]["plan"] if biz_row.data else "starter"

    clients_by_ledger = {
        c["tally_ledger_name"]: c
        for c in _fetch_all(lambda: db.table("clients")
                            .select("id, tally_ledger_name, name, whatsapp_number, language")
                            .eq("business_id", biz))
        if c.get("tally_ledger_name")
    }
    clients_by_id = {c["id"]: c for c in clients_by_ledger.values()}

    incoming = defaultdict(list)
    for b in payload.bills:
        c = clients_by_ledger.get(b.party_name)
        if c:
            incoming[c["id"]].append(b)

    # Ledger ClosingBalance per client_id = Tally's authoritative total owed.
    ledger_bal: dict[str, float] = {}
    for name, amt in (payload.ledger_balances or {}).items():
        c = clients_by_ledger.get(name)
        if c:
            ledger_bal[c["id"]] = round(float(amt or 0), 2)
    # Only trust ledger balances as the source of truth if the agent actually
    # sent them (older agents don't); otherwise fall back to bill-wise only.
    use_ledger = bool(payload.ledger_balances)

    target_ids = set(incoming.keys())
    for name in payload.all_parties:
        c = clients_by_ledger.get(name)
        if c:
            target_ids.add(c["id"])
    target_ids.update(ledger_bal.keys())

    # Existing open bills BEFORE upsert (for payment detection + reconcile).
    existing_bills: list = []
    for chunk in _chunked(list(target_ids), 100):
        existing_bills.extend(_fetch_all(lambda c=chunk: db.table("bills")
                              .select("id, client_id, tally_voucher_number, amount")
                              .eq("business_id", biz)
                              .in_("client_id", c)
                              .in_("status", ["pending", "partial", "overdue"])))
    old_amount = {e["tally_voucher_number"]: float(e.get("amount") or 0)
                  for e in existing_bills if e.get("tally_voucher_number")}

    # Build rows + snapshot + detect drops (payments).
    #
    # Per party the rule is: the LEDGER closing balance is the true total.
    #   - ledger says 0 (or party absent)  -> no rows; existing bills reconcile
    #     to 'paid' below (nothing owed).
    #   - bill-wise list reconciles to the ledger total (within Rs 1) -> keep the
    #     aged bills (accurate dates/overdue).
    #   - otherwise (no bill-wise data, or it doesn't add up: the ledger doesn't
    #     'maintain balances bill-by-bill', has advances, on-account receipts,
    #     etc.) -> ONE lump balance bill for the exact ledger total, due FY start.
    # This guarantees every party's dashboard total == Tally to the rupee.
    rows = []
    snap = defaultdict(set)
    seen: dict = {}
    payments: list = []              # (client_id, paid_delta, remaining)

    def _emit(cid, vnum, invoice_number, amount, inv, due):
        rows.append({
            "business_id": biz, "client_id": cid,
            "invoice_number": (invoice_number or vnum)[:60],
            "tally_voucher_number": vnum,
            "amount": round(float(amount), 2), "paid_amount": 0.0,
            "invoice_date": inv, "due_date": due or inv,
            "status": "pending", "is_opening_balance": inv < fy,
        })
        snap[cid].add(vnum)
        prev = old_amount.get(vnum)
        na = round(float(amount), 2)
        if prev is not None and na < prev - 0.99:
            payments.append((cid, round(prev - na, 2), na))

    for cid in target_ids:
        bl = incoming.get(cid, [])
        lb = ledger_bal.get(cid, 0.0)

        if use_ledger and lb <= 0:
            continue  # owes nothing per Tally's ledger -> reconcile away below

        bill_rows = []
        for b in sorted(bl, key=lambda x: ((x.bill_ref or ""), str(x.bill_date or ""), x.amount)):
            ref = (b.bill_ref or "").strip() or (b.bill_date or "x")
            base = f"TB-{cid}-{ref}"[:112]
            n = seen.get(base, 0)
            seen[base] = n + 1
            vnum = base if n == 0 else f"{base}#{n + 1}"
            inv = b.bill_date or fy
            bill_rows.append((vnum, ref[:60], round(float(b.amount), 2), inv, b.due_date or inv))
        bill_sum = round(sum(r[2] for r in bill_rows), 2)

        reconciles = bill_rows and (not use_ledger or abs(bill_sum - lb) <= 1.0)
        if reconciles:
            for vnum, ref, amt, inv, due in bill_rows:
                _emit(cid, vnum, ref, amt, inv, due)
        elif use_ledger:
            # Lump the exact ledger total so the party's dashboard matches Tally.
            _emit(cid, f"LB-{cid}", "Balance", lb, fy, fy)
        # else: old agent + no bill-wise data -> nothing to write for this party

    errors: list = []
    upserted = 0
    for chunk in _chunked(rows, 200):
        try:
            db.table("bills").upsert(chunk, on_conflict="business_id,tally_voucher_number").execute()
            upserted += len(chunk)
        except Exception as e:
            errors.append(f"upsert {len(chunk)} bills failed: {e}")

    # Reconcile: bills Tally no longer lists (paid off / old lumps) -> paid.
    stale = [e["id"] for e in existing_bills
             if e.get("tally_voucher_number") not in snap.get(e["client_id"], set())]
    marked_paid = 0
    for idchunk in _chunked(stale, 200):
        try:
            db.table("bills").update({"status": "paid"}).in_("id", idchunk).execute()
            marked_paid += len(idchunk)
        except Exception as e:
            errors.append(f"mark-paid {len(idchunk)} failed: {e}")

    # Customer payment confirmations (bounded, only if they have a number).
    confirmations = 0
    for cid, delta, remaining in payments[:30]:
        cl = clients_by_id.get(cid)
        if cl and cl.get("whatsapp_number"):
            background_tasks.add_task(_send_payment_confirmation, biz, plan_name, cl, delta, remaining)
            confirmations += 1

    # Stamp the sync so the dashboard's "last synced" is fresh every cycle (the
    # /sync endpoint only logs when there are vouchers; this runs every refresh).
    try:
        db.table("tally_syncs").insert({
            "business_id": biz, "sync_type": "poll",
            "records_synced": upserted, "success": len(errors) == 0,
            "error": "; ".join(errors)[:2000] if errors else None,
        }).execute()
    except Exception as e:
        log.error("Failed to log outstandings sync: %s", e)

    # Clear a pending manual "Reload data" request now that fresh data is in.
    try:
        db.table("businesses").update({"refresh_requested_at": None}).eq("id", biz).execute()
    except Exception:
        pass  # column may not exist yet (migration 015 not applied) - non-fatal

    return {
        "parties": len(target_ids),
        "bills_upserted": upserted,
        "bills_marked_paid": marked_paid,
        "payments_detected": len(payments),
        "confirmations_sent": confirmations,
        "errors": errors,
    }
