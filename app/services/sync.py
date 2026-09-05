"""Tally sync ingestion - vouchers, receipts, and outstanding data.

Called by the ``POST /tally/sync`` endpoint when the Windows agent pushes data.

Design decisions (from CTO audit):
  - Vouchers: UPSERT on (business_id, tally_voucher_number) - never duplicate bills
  - Receipts: apply payments via payments.py FIFO logic
  - Import mode: auto-create clients (via outstanding.py)
  - Regular sync mode: if ledger_name has no matching client, LOG the mismatch
    and skip - do NOT auto-create. Owner resolves mismatches manually.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from app.db import require_db
from app.models import TallySyncPayload, TallySyncResult
from app.services import outstanding as outstanding_service
from app.services import payments as payments_service

log = logging.getLogger(__name__)


async def _find_client_by_ledger(business_id: str, ledger_name: str) -> dict | None:
    """Look up a client by their exact Tally ledger name."""
    db = require_db()
    resp = (
        db.table("clients")
        .select("id, name, credit_days, whatsapp_number, language")
        .eq("business_id", business_id)
        .eq("tally_ledger_name", ledger_name.strip())
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _log_sync(
    business_id: str,
    sync_type: str,
    records: int,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Write a row to tally_syncs for audit / debugging."""
    db = require_db()
    db.table("tally_syncs").insert({
        "business_id": business_id,
        "sync_type": sync_type,
        "records_synced": records,
        "success": success,
        "error": error,
    }).execute()


async def ingest(payload: TallySyncPayload) -> TallySyncResult:
    """Process a sync payload from the Tally Windows agent.

    Handles three data types in one call:
      - ``vouchers`` - new sales invoices → upsert into ``bills``
      - ``receipts`` - payment receipts → apply via FIFO
      - ``outstanding`` - bulk outstanding snapshot (import mode only)
    """
    db = require_db()
    is_import = payload.sync_type.value == "import"

    # Shop's fallback credit period (trade-category default) for parties with no
    # Tally BillCreditPeriod. Fetched once; best-effort -> 30.
    from app.models import resolve_default_credit_days
    try:
        _bizrow = (db.table("businesses").select("category, default_credit_days")
                   .eq("id", payload.business_id).limit(1).execute()).data
        biz_cd = resolve_default_credit_days(_bizrow[0] if _bizrow else None)
    except Exception:
        biz_cd = 30

    bills_created = 0
    payments_applied = 0
    clients_created = 0
    errors: list[str] = []

    # ── Handle outstanding import (one-time onboarding) ───────────────
    if payload.outstanding and is_import:
        result = await outstanding_service.import_outstanding(
            payload.business_id,
            payload.outstanding,
        )
        bills_created += result.get("bills_imported", 0)
        clients_created += result.get("clients_created", 0)
        errors.extend(result.get("errors", []))

    # ── Handle new sales vouchers ─────────────────────────────────────
    for voucher in payload.vouchers:
        client = await _find_client_by_ledger(
            payload.business_id, voucher.ledger_name
        )

        if not client:
            if is_import:
                # Auto-create during import
                try:
                    new_resp = (
                        db.table("clients")
                        .insert({
                            "business_id": payload.business_id,
                            "name": voucher.ledger_name.strip(),
                            "tally_ledger_name": voucher.ledger_name.strip(),
                        })
                        .execute()
                    )
                    client = new_resp.data[0]
                    clients_created += 1
                except Exception as exc:
                    errors.append(
                        f"Client create failed for {voucher.ledger_name}: {exc}"
                    )
                    continue
            else:
                # Regular sync - log mismatch, do NOT auto-create
                error_msg = f"unmatched_ledger:{voucher.ledger_name}"
                errors.append(error_msg)
                log.warning(
                    "Unmatched ledger %r in business %s - skipping voucher %s",
                    voucher.ledger_name,
                    payload.business_id,
                    voucher.voucher_number,
                )
                continue

        # Calculate due_date from client credit period (else the shop default)
        credit_days = client.get("credit_days") or biz_cd
        due_date = voucher.date + timedelta(days=credit_days)

        bill_data = {
            "business_id": payload.business_id,
            "client_id": client["id"],
            "invoice_number": voucher.invoice_number or voucher.voucher_number,
            "amount": float(voucher.amount),
            "paid_amount": 0,
            "status": "pending",
            "invoice_date": voucher.date.isoformat(),
            "due_date": due_date.isoformat(),
            "tally_voucher_number": voucher.voucher_number,
        }

        try:
            # UPSERT - prevents duplicates on re-sync
            db.table("bills").upsert(
                bill_data,
                on_conflict="business_id,tally_voucher_number",
            ).execute()
            bills_created += 1
        except Exception as exc:
            errors.append(f"Bill upsert failed for {voucher.voucher_number}: {exc}")
            log.error("Bill upsert failed: %s - %s", voucher.voucher_number, exc)

    # ── Handle payment receipts ───────────────────────────────────────
    for receipt in payload.receipts:
        client = await _find_client_by_ledger(
            payload.business_id, receipt.ledger_name
        )
        if not client:
            errors.append(f"Receipt - unmatched_ledger:{receipt.ledger_name}")
            log.warning(
                "Receipt for unmatched ledger %r - skipping",
                receipt.ledger_name,
            )
            continue

        try:
            result = await payments_service.apply_payment(
                business_id=payload.business_id,
                client_id=client["id"],
                amount=receipt.amount,
                source="tally",
            )
            if result.get("applied"):
                payments_applied += 1
        except Exception as exc:
            errors.append(f"Payment apply failed for {receipt.ledger_name}: {exc}")
            log.error(
                "Payment apply failed: %s - %s", receipt.ledger_name, exc
            )

    # ── Broken-setup detection ────────────────────────────────────────
    # Tally is handing us real vouchers whose parties we cannot match, AND this
    # shop has no customers at all. That is not a mismatch to resolve by hand -
    # the debtor import has never worked (usually the customer group is not
    # called "Sundry Debtors", so the group walk returns nothing). Flag it, or
    # the shop sits at "0 parties / All clear" forever while every sync reports
    # success. Detected HERE because /tally/sync keeps ticking even when the
    # outstanding refresh bails out early, so this catches shops whose agent is
    # too old to report the problem itself.
    unmatched = [e for e in errors if "unmatched_ledger:" in e]
    if unmatched:
        try:
            n_clients = (db.table("clients").select("id", count="exact")
                         .eq("business_id", str(payload.business_id))
                         .limit(1).execute().count or 0)
            if n_clients == 0:
                from app.routers.tally import _flag_setup_problem
                _flag_setup_problem(
                    db, str(payload.business_id),
                    "Tally is sending ASVA your invoices, but ASVA cannot find "
                    "any customers in it. This usually means your customer group "
                    "is named something other than Sundry Debtors. Nothing in "
                    "Tally has been changed.")
        except Exception:
            log.debug("setup-problem detection skipped", exc_info=True)

    # ── Audit log ─────────────────────────────────────────────────────
    total_records = bills_created + payments_applied
    await _log_sync(
        payload.business_id,
        payload.sync_type.value,
        total_records,
        success=len(errors) == 0,
        error="; ".join(errors[:10]) if errors else None,
    )

    return TallySyncResult(
        bills_created=bills_created,
        payments_applied=payments_applied,
        clients_created=clients_created,
        errors=errors,
    )
