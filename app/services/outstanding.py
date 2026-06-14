"""One-click outstanding import for customer onboarding.

When a CA runs the Tally agent with ``--mode import`` for the first time,
this service receives ALL existing debtor balances and creates the full
client + bill state in one operation.

Design decisions (from CTO audit):
  - Auto-create clients during import (unlike regular sync which logs mismatches)
  - Set correct bill status: overdue if past due, pending if not
  - Track and return which clients are missing WhatsApp numbers
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from app.db import require_db
from app.models import TallyOutstandingRow

log = logging.getLogger(__name__)


async def import_outstanding(
    business_id: str,
    rows: list[TallyOutstandingRow],
) -> dict:
    """Bulk-import outstanding debtor balances from Tally.

    For each row:
      1. Find or create the client by ``ledger_name``
      2. Upsert the bill (keyed on ``business_id + invoice_number``)
      3. Set status to ``overdue`` or ``pending`` based on due date

    Returns:
        Summary with counts and a list of clients missing WhatsApp numbers.
    """
    db = require_db()

    # Fetch the default credit_days from the DB (30 if not set)
    DEFAULT_CREDIT_DAYS = 30

    clients_created = 0
    bills_imported = 0
    bills_skipped = 0
    missing_whatsapp: list[str] = []
    errors: list[str] = []

    # Pre-fetch all existing clients for this business to minimise queries
    existing_clients_resp = (
        db.table("clients")
        .select("id, tally_ledger_name, credit_days, whatsapp_number")
        .eq("business_id", business_id)
        .execute()
    )
    client_map: dict[str, dict] = {}
    for c in existing_clients_resp.data or []:
        if c.get("tally_ledger_name"):
            client_map[c["tally_ledger_name"].strip().lower()] = c

    for row in rows:
        ledger_key = row.ledger_name.strip().lower()

        # ── Find or create client ─────────────────────────────────────
        client = client_map.get(ledger_key)
        if not client:
            try:
                new_client_resp = (
                    db.table("clients")
                    .insert({
                        "business_id": business_id,
                        "name": row.ledger_name.strip(),
                        "tally_ledger_name": row.ledger_name.strip(),
                        "credit_days": DEFAULT_CREDIT_DAYS,
                    })
                    .execute()
                )
                client = new_client_resp.data[0]
                client_map[ledger_key] = client
                clients_created += 1

                # Track missing WhatsApp
                if not client.get("whatsapp_number"):
                    missing_whatsapp.append(row.ledger_name.strip())
            except Exception as exc:
                errors.append(f"Client create failed for {row.ledger_name}: {exc}")
                log.error("Client create failed: %s — %s", row.ledger_name, exc)
                continue
        else:
            if not client.get("whatsapp_number"):
                name = row.ledger_name.strip()
                if name not in missing_whatsapp:
                    missing_whatsapp.append(name)

        client_id = client["id"]
        credit_days = client.get("credit_days", DEFAULT_CREDIT_DAYS)

        # ── Calculate due_date and status ─────────────────────────────
        invoice_date = row.invoice_date
        due_date = invoice_date + timedelta(days=credit_days)
        status = "overdue" if due_date < date.today() else "pending"

        # ── Upsert bill ──────────────────────────────────────────────
        invoice_num = row.invoice_number or f"IMP-{row.ledger_name[:10]}-{invoice_date}"
        bill_data = {
            "business_id": business_id,
            "client_id": client_id,
            "invoice_number": invoice_num,
            "amount": float(row.amount),
            "paid_amount": 0,
            "status": status,
            "invoice_date": invoice_date.isoformat(),
            "due_date": due_date.isoformat(),
            "tally_voucher_number": invoice_num,
        }

        try:
            db.table("bills").upsert(
                bill_data,
                on_conflict="business_id,tally_voucher_number",
            ).execute()
            bills_imported += 1
        except Exception as exc:
            bills_skipped += 1
            errors.append(f"Bill upsert failed for {invoice_num}: {exc}")
            log.error("Bill upsert failed: %s — %s", invoice_num, exc)

    # ── Write audit log ──────────────────────────────────────────────
    db.table("tally_syncs").insert({
        "business_id": business_id,
        "sync_type": "import",
        "records_synced": bills_imported,
        "success": len(errors) == 0,
        "error": "; ".join(errors[:5]) if errors else None,
    }).execute()

    summary = {
        "clients_created": clients_created,
        "bills_imported": bills_imported,
        "bills_skipped": bills_skipped,
        "missing_whatsapp": len(missing_whatsapp),
        "missing_whatsapp_names": missing_whatsapp,
        "errors": errors,
        "message": (
            f"{bills_imported} bills imported, {clients_created} clients created. "
            f"{len(missing_whatsapp)} clients without WhatsApp numbers — "
            "add numbers to start reminders."
            if missing_whatsapp
            else f"{bills_imported} bills imported, {clients_created} clients created."
        ),
    }

    log.info(
        "Outstanding import for %s: %d bills, %d clients, %d missing numbers",
        business_id,
        bills_imported,
        clients_created,
        len(missing_whatsapp),
    )
    return summary
