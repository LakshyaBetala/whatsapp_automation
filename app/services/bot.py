"""WhatsApp bot command parser.

Handles inbound messages from business owners and their customers.
Commands are parsed with regex first. Gemini fallback is Phase 3.

# Bot replies are always Hindi — owner language is Hindi by default.
# Client language (Gujarati, Marathi) is only for outbound reminders.

Security rule (from CTO audit):
  - PAID from owner number → mark paid immediately
  - PAID from customer number → notify owner to confirm, do NOT auto-mark
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import payments as payments_service
from app.services import whatsapp
from app.services.templates import inr

log = logging.getLogger(__name__)


async def handle(from_number: str, text: str) -> str:
    """Route an inbound WhatsApp message to the right handler.

    Args:
        from_number: Sender's WhatsApp number (E.164 without +, e.g. 919876543210).
        text: Message body, already stripped.

    Returns:
        Reply text to send back (via AiSensy or log in dev mode).
    """
    db = require_db()
    upper = text.upper().strip()

    # ── Identify sender: owner or customer? ───────────────────────────
    biz_resp = (
        db.table("businesses")
        .select("id, business_name, plan, whatsapp_number")
        .eq("whatsapp_number", from_number)
        .limit(1)
        .execute()
    )
    is_owner = bool(biz_resp.data)
    business = biz_resp.data[0] if is_owner else None

    if is_owner:
        business_id = business["id"]

        # ── LIST ──────────────────────────────────────────────────────
        if upper == "LIST":
            return await _handle_list(business_id, business["business_name"])

        # ── STOP <name> ──────────────────────────────────────────────
        stop_match = re.match(r"STOP\s+(.+)", upper)
        if stop_match:
            client_name = stop_match.group(1).strip()
            return await _handle_stop(business_id, client_name)

        # ── START <name> ─────────────────────────────────────────────
        start_match = re.match(r"START\s+(.+)", upper)
        if start_match:
            client_name = start_match.group(1).strip()
            return await _handle_start(business_id, client_name)

        # ── PAID <name> ──────────────────────────────────────────────
        paid_match = re.match(r"PAID\s+(.+)", upper)
        if paid_match:
            client_name = paid_match.group(1).strip()
            return await _handle_paid_owner(
                business_id, client_name, Plan(business["plan"])
            )

        # ── CHECK <name> — live balance, matches Tally to the rupee ──
        check_match = re.match(r"CHECK\s+(.+)", upper)
        if check_match:
            return await _handle_check(business_id, check_match.group(1).strip())

        # ── Unrecognised ──────────────────────────────────────────────
        return (
            "Command samajh nahi aaya. Try:\n"
            "LIST — outstanding list\n"
            "CHECK [naam] — ek party ka balance\n"
            "STOP [naam] — reminders band\n"
            "START [naam] — reminders chalu\n"
            "PAID [naam] — payment mark"
        )

    # ── Customer message (not owner) ──────────────────────────────────
    # Look up which business this customer belongs to
    client_resp = (
        db.table("clients")
        .select("id, name, business_id")
        .eq("whatsapp_number", from_number)
        .limit(1)
        .execute()
    )

    if not client_resp.data:
        log.info("Message from unknown number %s: %s", from_number, text)
        return ""  # Don't reply to unknown numbers

    client = client_resp.data[0]

    # ── Customer says PAID ────────────────────────────────────────────
    if upper == "PAID" or upper.startswith("PAID"):
        return await _handle_paid_customer(client)

    # Ignore other customer messages for now
    return ""


async def _handle_list(business_id: str, business_name: str) -> str:
    """Return a summary of outstanding bills grouped by client."""
    db = require_db()
    bills_resp = (
        db.table("bills")
        .select("client_id, outstanding, clients(name)")
        .eq("business_id", business_id)
        .in_("status", ["pending", "partial", "overdue"])
        .order("outstanding", desc=True)
        .execute()
    )

    if not bills_resp.data:
        return f"{business_name} — koi outstanding nahi hai. Sab clear! 🎉"

    # Group by client
    client_totals: dict[str, dict] = {}
    for row in bills_resp.data:
        client_name = row.get("clients", {}).get("name", "Unknown")
        outstanding = Decimal(str(row["outstanding"]))
        if client_name in client_totals:
            client_totals[client_name]["total"] += outstanding
            client_totals[client_name]["count"] += 1
        else:
            client_totals[client_name] = {"total": outstanding, "count": 1}

    # Sort by total outstanding descending
    sorted_clients = sorted(
        client_totals.items(), key=lambda x: x[1]["total"], reverse=True
    )

    grand_total = sum(c["total"] for c in client_totals.values())
    lines = [f"{business_name} — Outstanding List\n"]
    for i, (name, data) in enumerate(sorted_clients[:20], 1):
        lines.append(f"{i}. {name} — {inr(data['total'])} ({data['count']} bills)")

    if len(sorted_clients) > 20:
        lines.append(f"\n...aur {len(sorted_clients) - 20} aur hain")

    lines.append(f"\nTotal: {inr(grand_total)}")
    return "\n".join(lines)


async def _handle_stop(business_id: str, client_name: str) -> str:
    """Pause reminders for a client by fuzzy name match."""
    db = require_db()
    # Case-insensitive partial match
    clients_resp = (
        db.table("clients")
        .select("id, name, reminders_enabled")
        .eq("business_id", business_id)
        .ilike("name", f"%{client_name}%")
        .execute()
    )

    if not clients_resp.data:
        return f"'{client_name}' naam ka koi client nahi mila. Exact naam likhein."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' se kai clients mile: {names}. Poora naam likhein."

    client = clients_resp.data[0]
    if not client["reminders_enabled"]:
        return f"{client['name']} ke reminders pehle se band hain."

    db.table("clients").update({"reminders_enabled": False}).eq(
        "id", client["id"]
    ).execute()

    return f"{client['name']} ke reminders band kar diye. Chalu karne ke liye START {client['name']} bhejein."


async def _handle_start(business_id: str, client_name: str) -> str:
    """Resume reminders for a client by fuzzy name match."""
    db = require_db()
    clients_resp = (
        db.table("clients")
        .select("id, name, reminders_enabled")
        .eq("business_id", business_id)
        .ilike("name", f"%{client_name}%")
        .execute()
    )

    if not clients_resp.data:
        return f"'{client_name}' naam ka koi client nahi mila. Exact naam likhein."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' se kai clients mile: {names}. Poora naam likhein."

    client = clients_resp.data[0]
    if client["reminders_enabled"]:
        return f"{client['name']} ke reminders pehle se chalu hain."

    db.table("clients").update({"reminders_enabled": True}).eq(
        "id", client["id"]
    ).execute()

    return f"{client['name']} ke reminders chalu kar diye. ✅"


async def _handle_check(business_id: str, client_name: str) -> str:
    """Instant per-party statement — the anti-'sync is broken' command.

    Answers with open bills + total + last sync time so the owner can
    verify against Tally to the rupee, any time, in 5 seconds.
    """
    db = require_db()
    clients_resp = (
        db.table("clients")
        .select("id, name, whatsapp_number, reminders_enabled")
        .eq("business_id", business_id)
        .ilike("name", f"%{client_name}%")
        .execute()
    )
    if not clients_resp.data:
        return f"'{client_name}' naam ka koi client nahi mila. Exact naam likhein."
    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' se kai clients mile: {names}. Poora naam likhein."

    client = clients_resp.data[0]
    bills_resp = (
        db.table("bills")
        .select("invoice_number, outstanding, due_date, status")
        .eq("business_id", business_id)
        .eq("client_id", client["id"])
        .in_("status", ["pending", "partial", "overdue"])
        .order("due_date")
        .execute()
    )
    open_bills = bills_resp.data or []

    from datetime import date as _date
    lines = [f"{client['name']}"]
    if not open_bills:
        lines.append("Koi outstanding nahi — sab clear ✅")
    else:
        total = Decimal(0)
        for b in open_bills[:8]:
            amt = Decimal(str(b["outstanding"]))
            total += amt
            overdue = ""
            if b.get("due_date"):
                days = (_date.today() - _date.fromisoformat(str(b["due_date"]))).days
                if days > 0:
                    overdue = f" ({days} din overdue)"
            lines.append(f"• {b.get('invoice_number') or '—'}: {inr(amt)}{overdue}")
        if len(open_bills) > 8:
            rest = sum(Decimal(str(b["outstanding"])) for b in open_bills[8:])
            total += rest
            lines.append(f"• ...aur {len(open_bills) - 8} bills: {inr(rest)}")
        lines.append(f"Total baaki: {inr(total)}")

    phone = client.get("whatsapp_number")
    lines.append(f"WhatsApp: {phone if phone else '❌ number nahi hai'}")
    lines.append(f"Reminders: {'chalu' if client.get('reminders_enabled', True) else 'band'}")

    # Last sync time — proof of freshness
    sync_resp = (
        db.table("tally_syncs")
        .select("synced_at")
        .eq("business_id", business_id)
        .order("synced_at", desc=True)
        .limit(1)
        .execute()
    )
    if sync_resp.data:
        lines.append(f"Tally se milaya: {str(sync_resp.data[0]['synced_at'])[:16]}")
    return "\n".join(lines)


async def _handle_paid_owner(
    business_id: str, client_name: str, plan: Plan
) -> str:
    """Owner confirmed payment — mark paid immediately."""
    db = require_db()
    clients_resp = (
        db.table("clients")
        .select("id, name")
        .eq("business_id", business_id)
        .ilike("name", f"%{client_name}%")
        .execute()
    )

    if not clients_resp.data:
        return f"'{client_name}' naam ka koi client nahi mila."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' se kai clients mile: {names}. Poora naam likhein."

    client = clients_resp.data[0]

    # Find oldest open bill to get amount
    bill_resp = (
        db.table("bills")
        .select("outstanding")
        .eq("business_id", business_id)
        .eq("client_id", client["id"])
        .in_("status", ["pending", "partial", "overdue"])
        .order("invoice_date", desc=False)
        .limit(1)
        .execute()
    )

    if not bill_resp.data:
        return f"{client['name']} ka koi outstanding bill nahi hai."

    outstanding = Decimal(str(bill_resp.data[0]["outstanding"]))
    result = await payments_service.apply_payment(
        business_id=business_id,
        client_id=client["id"],
        amount=outstanding,
        source="bot",
    )

    if result.get("applied"):
        return (
            f"{client['name']} ka {inr(outstanding)} payment mark ho gaya. "
            f"{result['bills_affected']} bill(s) updated."
        )
    return f"Payment apply nahi ho paya: {result.get('reason', 'unknown error')}"


async def _handle_paid_customer(client: dict) -> str:
    """Customer claimed payment — do NOT auto-mark. Notify owner to confirm.

    Security: anyone who knows the WhatsApp number could send PAID.
    Only the owner can actually mark bills as paid.
    """
    db = require_db()
    business_id = client["business_id"]

    # Notify owner to confirm
    await whatsapp.notify_owner(
        business_id,
        f"{client['name']} ne PAID reply kiya. Confirm karo: PAID {client['name']}",
    )

    return (
        "Shukriya! Aapka payment note kar liya hai. "
        "Business owner ko inform kar diya gaya hai. 🙏"
    )
