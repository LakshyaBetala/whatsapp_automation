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
    tally_guid: Optional[str] = None       # Tally's stable ledger id (rename-proof)


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


def _resolve_client(db, clients_by_ledger: dict, clients_by_guid: dict,
                    name: str, guid: Optional[str]):
    """Find the client for a Tally party by its stable GUID first, then by name.

    This is the permanent duplicate fix: Tally's GUID never changes when a shop
    renames a party or edits their number, so:
      1. GUID match -> the SAME client, even under a new name; propagate the
         rename onto the row (name + tally_ledger_name) so bills still match.
      2. No GUID match but name matches -> a legacy row from before GUIDs were
         stored; backfill the GUID so every future rename matches by GUID.
      3. Neither -> None (the caller creates a fresh client).
    Keeps both lookup maps consistent so later bill matching (by ledger name)
    sees the updated name. Returns the client dict or None."""
    guid = (guid or "").strip() or None
    name = (name or "").strip()
    if guid and guid in clients_by_guid:
        c = clients_by_guid[guid]
        if name and c.get("tally_ledger_name") != name:
            old = c.get("tally_ledger_name")
            try:
                db.table("clients").update(
                    {"tally_ledger_name": name, "name": name}).eq("id", c["id"]).execute()
            except Exception:
                log.exception("client rename update failed for %s", c["id"])
            if old and clients_by_ledger.get(old) is c:
                clients_by_ledger.pop(old, None)
            c["tally_ledger_name"] = name
            c["name"] = name
            clients_by_ledger[name] = c
        return c
    c = clients_by_ledger.get(name)
    if c and guid and not c.get("tally_guid"):
        try:
            db.table("clients").update({"tally_guid": guid}).eq("id", c["id"]).execute()
            c["tally_guid"] = guid
            clients_by_guid[guid] = c
        except Exception:
            log.exception("client guid backfill failed for %s", c["id"])
    return c


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


# ── Payment-entry pipeline (PAID -> queue -> confirm -> Tally) ────────────────
class DepositLedgersPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    ledgers: List[str] = []


@router.post("/deposit-ledgers")
async def push_deposit_ledgers(payload: DepositLedgersPayload):
    """The agent reports the shop's Cash/Bank ledger names (read from Tally) so
    the confirm popup can offer the owner their own deposit accounts."""
    db = _verify_token(payload.business_id, payload.agent_token)
    from app.services import receipts_queue as rq
    rq.set_deposit_ledgers(db, str(payload.business_id), payload.ledgers)
    return {"stored": len(payload.ledgers or [])}


def _receipt_out(rows: list) -> dict:
    return {"receipts": [{
        "id": r["id"],
        "party_ledger": r["party_ledger"],
        "party_display": r.get("party_display"),
        "amount": float(r["amount"]),
        "deposit_ledger": r.get("deposit_ledger") or "Cash",
        "receipt_date": str(r.get("receipt_date") or "")[:10],
    } for r in rows]}


@router.get("/receipts/confirmed")
async def receipts_confirmed(business_id: uuid.UUID, agent_token: str):
    """Read-only peek at owner-approved receipts (does NOT claim them)."""
    db = _verify_token(business_id, agent_token)
    from app.services import receipts_queue as rq
    return _receipt_out(rq.list_confirmed(db, str(business_id)))


class ClaimPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str


@router.post("/receipts/claim")
async def receipts_claim(payload: ClaimPayload):
    """The agent CLAIMS confirmed receipts for posting: they flip confirmed ->
    posting and are returned once. A claimed (posting) receipt is never handed out
    again, so a lost report never causes a double post into Tally. The agent then
    posts each and reports posted/failed."""
    db = _verify_token(payload.business_id, payload.agent_token)
    from app.services import receipts_queue as rq
    return _receipt_out(rq.claim_confirmed(db, str(payload.business_id)))


class ReceiptReportPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    id: str
    ok: bool
    voucher_id: Optional[str] = None
    error: Optional[str] = None
    allocation: Optional[list] = None


@router.post("/receipts/report")
async def receipts_report(payload: ReceiptReportPayload):
    """The agent reports the outcome of writing a confirmed receipt into Tally.
    Success -> 'posted' (keeps the voucher id + allocation for revert/audit);
    failure -> 'failed' (with the error surfaced to the owner in the app)."""
    db = _verify_token(payload.business_id, payload.agent_token)
    from app.services import receipts_queue as rq
    if payload.ok:
        rq.mark(db, payload.id, "posted", voucher_id=payload.voucher_id,
                allocation=payload.allocation)
    else:
        rq.mark(db, payload.id, "failed", error=payload.error or "Tally rejected the entry")
    return {"recorded": True}


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
    all_clients = _fetch_all(lambda: db.table("clients")
                             .select("id, tally_ledger_name, whatsapp_number, credit_days, tally_guid")
                             .eq("business_id", biz))
    existing_clients = {
        c["tally_ledger_name"]: c for c in all_clients if c.get("tally_ledger_name")
    }
    clients_by_guid = {c["tally_guid"]: c for c in all_clients if c.get("tally_guid")}
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

            # Match by stable GUID first (rename-proof), then name; backfills the
            # GUID onto legacy rows and propagates a Tally rename to the same row.
            existing = _resolve_client(db, existing_clients, clients_by_guid,
                                       debtor.name, debtor.tally_guid)
            if existing is None:
                row = {
                    "business_id": biz,
                    "name": debtor.name,
                    "tally_ledger_name": debtor.name,
                    "tally_group": debtor.tally_group,
                    "whatsapp_number": phone,
                    "tally_guid": (debtor.tally_guid or "").strip() or None,
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
                # Tally is the source of truth for the customer's number: set it
                # if we have none, OR if it changed in Tally (the shop corrected
                # or updated the mobile). Only overwrite when Tally actually has a
                # number, so a blank Tally field never wipes an existing one.
                if phone and phone != existing.get("whatsapp_number"):
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

    # Renames applied above may have re-keyed existing_clients; make sure the
    # OB lookup below can find every party by its current name.
    ledger_to_id.update({name: c["id"] for name, c in existing_clients.items()})

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
    pdf_skipped = []  # exported PDFs we did NOT send + the reason (owner feedback)

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
                if v.pdf_base64:
                    pdf_skipped.append({"voucher": v.voucher_number,
                                        "reason": f"customer '{v.party_name}' is not in ASVA yet - "
                                                  f"press Reload data, then re-export the bill"})
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
                # A PDF is attached ONLY when the owner physically exported this
                # bill into the pickup folder - always a deliberate act - so we
                # deliver it whenever it can go, and tell the owner the exact
                # reason when it can't (no number / too old / already sent). The
                # 35-day window (was 10) keeps the whole FY replay from blasting
                # historic invoices at first onboarding while still letting a bill
                # the owner exported weeks ago go out.
                already_sent = bool(bill_row.get("pdf_url"))
                fresh = invoice_date >= date.today() - timedelta(days=35)
                if v.pdf_base64 and already_sent:
                    pass  # sent before - silent, not a problem to report
                elif v.pdf_base64 and not client.get("whatsapp_number"):
                    pdf_skipped.append({"voucher": v.voucher_number,
                                        "reason": "this customer has no WhatsApp number in Tally - "
                                                  "add their mobile in Tally, then re-export"})
                elif v.pdf_base64 and not fresh:
                    pdf_skipped.append({"voucher": v.voucher_number,
                                        "reason": "bill is older than 35 days - not sent automatically"})
                elif v.pdf_base64 and not already_sent and fresh and client.get("whatsapp_number"):
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
                    "client_id": client_id,   # link by id (rename-proof), migration 033
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

                    bill_update = {"paid_amount": new_paid, "status": new_status}
                    if new_status == "paid":
                        # True settlement date = the receipt's own date (the day the
                        # money came), NOT now() - so days-to-pay is honest. Only
                        # stamped when the bill is FULLY cleared (migration 034).
                        bill_update["settled_at"] = str(v.date)
                    db.table("bills").update(bill_update).eq("id", b["id"]).execute()

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
        "pdf_skipped": pdf_skipped,
        "errors": errors
    }


class TallyOpenBill(BaseModel):
    party_name: str
    bill_ref: str = ""
    bill_date: Optional[str] = None   # YYYY-MM-DD
    due_date: Optional[str] = None    # YYYY-MM-DD
    amount: float                     # Tally's NET outstanding for this bill


class TallyContact(BaseModel):
    name: str                              # tally ledger name
    whatsapp_number: Optional[str] = None  # agent-extracted number (field/alias/address)
    tally_guid: Optional[str] = None       # stable ledger id (rename-proof)


class TallyParty(BaseModel):
    """A Sundry-Debtor ledger, sent on every refresh so the backend can create
    parties added in Tally AFTER the one-time import."""
    name: str
    whatsapp_number: Optional[str] = None
    credit_days: Optional[int] = None
    tally_group: Optional[str] = None
    tally_guid: Optional[str] = None       # stable ledger id (rename-proof)


def _sync_contacts(db, biz: str, contacts: list, clients_by_ledger: dict) -> int:
    """Keep customer WhatsApp numbers in step with Tally (the source of truth).
    Runs every refresh but is cheap: only the few clients whose number actually
    changed get written. Update-only - never creates clients here."""
    updated = 0
    for ct in contacts or []:
        phone = _normalize_phone(ct.whatsapp_number)
        if not phone:
            continue
        c = clients_by_ledger.get(ct.name)
        if c and phone != c.get("whatsapp_number"):
            try:
                db.table("clients").update({"whatsapp_number": phone}).eq("id", c["id"]).execute()
                c["whatsapp_number"] = phone
                updated += 1
            except Exception:
                log.exception("contact number sync failed for %s", ct.name)
    if updated:
        log.info("Synced %d customer WhatsApp number(s) from Tally", updated)
    return updated


class TallyOutstandingsPayload(BaseModel):
    business_id: uuid.UUID
    agent_token: str
    company_name: str
    bills: list[TallyOpenBill]
    all_parties: list[str] = []       # every debtor ledger (to clear fully-paid ones)
    contacts: list[TallyContact] = [] # {name, whatsapp_number} - keeps numbers current
    parties: list[TallyParty] = []    # full debtor details -> auto-create new ones
    # Ledger ClosingBalance per party (Tally's authoritative "what they owe
    # today" total). This is the source of truth for the amount; the bill-wise
    # list above only breaks it into aged bills WHEN it reconciles. Sending
    # this fixes parties whose ledgers don't 'maintain balances bill-by-bill'
    # (they return zero bills, so without this they'd wrongly show as cleared).
    ledger_balances: dict[str, float] = {}


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

    _all_clients = _fetch_all(lambda: db.table("clients")
                              .select("id, tally_ledger_name, name, whatsapp_number, language, tally_guid")
                              .eq("business_id", biz))
    clients_by_ledger = {
        c["tally_ledger_name"]: c for c in _all_clients if c.get("tally_ledger_name")
    }
    clients_by_guid = {c["tally_guid"]: c for c in _all_clients if c.get("tally_guid")}
    clients_by_id = {c["id"]: c for c in clients_by_ledger.values()}

    # ── Auto-create parties added in Tally AFTER the one-time import ──────
    # Previously only /tally/import (one-time) created clients, so a NEW customer
    # and their bill never appeared in ASVA. Now every refresh sends the full
    # Sundry-Debtor list (payload.parties); any not already known is created here,
    # then its bills flow through the normal upsert below (and /tally/sync delivers
    # the new bill on its next tick).
    created_parties = 0
    if payload.parties:
        new_rows = []
        for p in payload.parties:
            if not p.name:
                continue
            # Resolve by GUID first: this propagates a Tally rename onto the SAME
            # client and backfills the GUID onto legacy rows - so a renamed party
            # is NEVER re-created as a duplicate. Only genuinely new parties fall
            # through to creation.
            existing = _resolve_client(db, clients_by_ledger, clients_by_guid,
                                       p.name, p.tally_guid)
            if existing is not None:
                continue
            cd = p.credit_days if (p.credit_days and 1 <= p.credit_days <= 365) else None
            row = {
                "business_id": biz,
                "name": p.name,
                "tally_ledger_name": p.name,
                "tally_group": p.tally_group,
                "whatsapp_number": _normalize_phone(p.whatsapp_number),
                "tally_guid": (p.tally_guid or "").strip() or None,
                "reminders_enabled": True,
            }
            if cd:
                row["credit_days"] = cd
            new_rows.append(row)
        for chunk in _chunked(new_rows, 200):
            try:
                res = db.table("clients").insert(chunk).execute()
                for c in (res.data or []):
                    if c.get("tally_ledger_name"):
                        clients_by_ledger[c["tally_ledger_name"]] = c
                        clients_by_id[c["id"]] = c
                        if c.get("tally_guid"):
                            clients_by_guid[c["tally_guid"]] = c
                        created_parties += 1
            except Exception:
                log.exception("auto-create of new Tally parties failed (business %s)", biz)
        if created_parties:
            log.info("Auto-created %d new party(ies) from Tally refresh", created_parties)

    # Refresh customer numbers from Tally before anything else (cheap, targeted).
    _sync_contacts(db, biz, payload.contacts, clients_by_ledger)

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

    # Payments detected via Tally update the ledger SILENTLY. We deliberately do
    # NOT message the customer "received Rs X" - that fired on every refresh a
    # balance moved and felt like spam ("paid Rs 500" to everyone, repeatedly).
    # The owner records the receipt in Tally; the dashboard reflects it. The only
    # payment-related customer message is the "Thank you" reply when THEY send PAID.
    confirmations = 0

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
        "new_parties": created_parties,
        "bills_upserted": upserted,
        "bills_marked_paid": marked_paid,
        "payments_detected": len(payments),
        "confirmations_sent": confirmations,
        "errors": errors,
    }
