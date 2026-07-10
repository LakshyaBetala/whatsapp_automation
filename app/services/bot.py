"""WhatsApp bot command parser.

Handles inbound messages from business owners and their customers.
Commands are parsed with regex first. Gemini fallback is Phase 3.

# Bot replies are always Hindi - owner language is Hindi by default.
# Client language (Gujarati, Marathi) is only for outbound reminders.

Security rule (from CTO audit):
  - PAID from owner number → mark paid immediately
  - PAID from customer number → notify owner to confirm, do NOT auto-mark
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from datetime import date

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import payments as payments_service
from app.services import upi, whatsapp
from app.services.templates import apply_discount, inr


async def _forward_to_team(business_id: str, business_name: str, from_number: str, message: str) -> str:
    """Forward an owner's support request to the ASVA product team."""
    team = settings.product_team_number
    if not team:
        return "Aapki baat note kar li. Team jaldi aapse contact karegi."
    try:
        await whatsapp.send_message(
            business_id=business_id,
            to_number=team,
            message_text=f"[ASVA support] {business_name} ({from_number}): {message}",
            message_type=MessageType.owner_alert,
            channel="platform",
        )
    except Exception:
        log.exception("Failed to forward support message to team")
    return "Aapka message ASVA team tak pahuncha diya. Jaldi jawab milega."

log = logging.getLogger(__name__)

# Greeting / help keywords that should ALWAYS get a reply, even from a number
# we do not recognise (so a new customer or a pilot tester is never ghosted).
_GREETING = ("HI", "HELLO", "HELP", "MENU", "START", "?", "HEY", "NAMASTE")


def _last10(n: str) -> str:
    """Last 10 digits of a phone number - used as a format-agnostic match key
    so a number stored as 91XXXXXXXXXX still matches +91, 0-prefixed, etc."""
    d = "".join(c for c in str(n or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _match_row(db, table: str, select: str, from_number: str):
    """Find a row in `table` whose whatsapp_number matches `from_number`.
    Exact match first, then a last-10-digit fallback (format drift safety)."""
    r = db.table(table).select(select).eq("whatsapp_number", from_number).limit(1).execute()
    if r.data:
        return r.data[0]
    last10 = _last10(from_number)
    if last10:
        r = db.table(table).select(select).like("whatsapp_number", f"%{last10}").limit(1).execute()
        if r.data:
            return r.data[0]
    return None


async def handle(
    from_number: str,
    text: str,
    media_b64: str | None = None,
    media_type: str = "image/jpeg",
) -> str:
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
    business = _match_row(
        db, "businesses",
        "id, business_name, plan, whatsapp_number, upi_vpa, discount_pct, msg_language",
        from_number)
    is_owner = business is not None

    if is_owner:
        business_id = business["id"]

        # ── Photo of a bill → OCR → confirm flow ─────────────────────
        if media_b64:
            return await _handle_photo_bill(business, media_b64, media_type)

        # ── Photo-bill confirmation / correction commands ─────────────
        if upper in ("YES", "HAAN", "HA", "OK", "CONFIRM"):
            return await _confirm_photo_bill(business)
        if upper == "CANCEL":
            return await _cancel_photo_bill(business_id)
        fix_match = re.match(r"(NAAM|PHONE|AMOUNT)\s+(.+)", upper)
        if fix_match:
            return await _correct_photo_bill(
                business_id, fix_match.group(1), text.strip().split(None, 1)[1])

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

        # ── CHECK <name> - live balance, matches Tally to the rupee ──
        check_match = re.match(r"CHECK\s+(.+)", upper)
        if check_match:
            return await _handle_check(business_id, check_match.group(1).strip())

        # ── TERMS <name> <days> - set a party's credit period ────────
        terms_match = re.match(r"TERMS\s+(.+?)\s+(\d{1,3})$", upper)
        if terms_match:
            return await _handle_terms(
                business_id, terms_match.group(1).strip(), int(terms_match.group(2)))

        # ── REMIND - owner decides who gets reminded, right now ──────
        #    REMIND <naam>      one party (consolidated bills + QR)
        #    REMIND TOP [n]     n biggest outstanding parties
        #    REMIND OLDEST [n]  n longest-pending parties
        remind_match = re.match(r"REMIND\s+(.+)", upper)
        if remind_match:
            return await _handle_remind(business, remind_match.group(1).strip())

        # ── TEAM / SUPPORT <message> - reach the ASVA product team ───
        team_match = re.match(r"(?:TEAM|SUPPORT|PROBLEM|MADAD)\s+(.+)", text.strip(), re.IGNORECASE)
        if team_match:
            return await _forward_to_team(
                business_id, business.get("business_name", ""), from_number, team_match.group(1).strip())

        # ── HELP / unrecognised ──────────────────────────────────────
        prefix = "" if upper in ("HELP", "MENU", "?", "HI", "HELLO", "START") else "Ye command samajh nahi aaya.\n\n"
        return (
            prefix
            + "Namaste! ASVA aapki udhaari recover karne me madad karta hai.\n\n"
            "Ye likhkar bhejein:\n"
            "LIST : poori baaki list\n"
            "CHECK Ramesh : ek party ka hisaab\n"
            "REMIND Ramesh : usko abhi reminder\n"
            "REMIND TOP 5 : sabse bade 5 baaki walon ko\n"
            "PAID Ramesh : uska payment mark karein\n"
            "TERMS Ramesh 90 : credit period 90 din\n"
            "STOP Ramesh / START Ramesh : reminder band ya chalu\n"
            "Bill ki photo bhejein : naya bill ban jayega\n\n"
            "Koi dikkat ho? likhein: TEAM aapki baat\n"
            "(Ramesh ki jagah apni party ka naam likhein.)"
        )

    # ── Customer message (not owner) ──────────────────────────────────
    # Look up which business this customer belongs to (format-agnostic match)
    client = _match_row(db, "clients", "id, name, business_id", from_number)

    if not client:
        # Unknown sender. Reply ONLY to an explicit greeting/help keyword so a
        # new customer or a pilot tester is never ghosted - but stay silent on
        # everything else, so the bot never auto-replies to ordinary chatter
        # (which would be intrusive and risk the number being flagged).
        if upper in _GREETING:
            return (
                "Namaste! Main is dukaan ka WhatsApp assistant hoon.\n\n"
                "Apna hisaab dekhne ke liye HISAB bhejein.\n"
                "Payment ki khabar dene ke liye PAID bhejein.\n\n"
                "Aapka number abhi hamare records me nahi mila. Dukaan se baat "
                "karne ke liye TEAM likhkar apni baat bhejein."
            )
        log.info("Message from unknown number %s: %s", from_number, text)
        return ""  # stay silent on non-greeting messages from unknown numbers

    # ── Customer says PAID ────────────────────────────────────────────
    if upper == "PAID" or upper.startswith("PAID"):
        return await _handle_paid_customer(client)

    # ── Customer self-service (keyword-only: this number is ALSO the
    #    shop's normal chat, so the bot must stay silent on normal talk) ─
    if upper in ("HISAB", "HISAAB", "BALANCE", "BAKI", "BAAKI", "1"):
        return await _customer_statement(client)
    # ── Customer support: forward to the SHOP owner (not the product team) ─
    cteam = re.match(r"(?:TEAM|SUPPORT|PROBLEM|MADAD|COMPLAINT)\s+(.+)", text.strip(), re.IGNORECASE)
    if cteam:
        try:
            await whatsapp.notify_owner(
                client["business_id"],
                f"{client['name']} ({from_number}) ne message bheja: {cteam.group(1).strip()}")
        except Exception:
            log.exception("Failed to forward customer message to owner")
        return "Aapki baat dukaan tak pahuncha di. Jaldi jawab milega."

    if upper in ("MENU", "HELP", "?", "HI", "HELLO"):
        return (
            f"Namaste {client['name']} ji!\n"
            "Main is dukaan ka assistant hoon.\n\n"
            "HISAB : apna poora baaki dekhein\n"
            "PAID : payment ki khabar dein\n\n"
            "Koi sawaal ho to likhein: TEAM aapki baat\n"
            "Dukaan tak pahuncha denge."
        )

    # Stay silent on everything else: a human will reply
    return ""


# ══════════════════════════════════════════════════════════════════════
# Photo bills (OCR): photo → extract → owner confirms/corrects → bill
# ══════════════════════════════════════════════════════════════════════

def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits
    return None


def _photo_bill_summary(pb: dict) -> str:
    amount = inr(Decimal(str(pb["amount"]))) if pb.get("amount") else "❓ nahi mila"
    lines = [
        "📸 Bill se yeh mila:",
        "",
        f"Party: {pb.get('party_name') or '❓ nahi mila'}",
        f"Phone: {pb.get('phone') or '❓ nahi mila'}",
        f"Amount: {amount}",
    ]
    if pb.get("bill_number"):
        lines.append(f"Bill no: {pb['bill_number']}")
    lines += [
        "",
        "Sahi hai? YES bhejein.",
        "Galti hai? Aise sudhaarein:",
        "NAAM Ramesh Traders",
        "PHONE 9876543210",
        "AMOUNT 12500",
        "Ya CANCEL bhejein.",
    ]
    return "\n".join(lines)


async def _latest_pending_photo_bill(business_id: str) -> dict | None:
    db = require_db()
    resp = (
        db.table("photo_bills")
        .select("*")
        .eq("business_id", business_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _handle_photo_bill(business: dict, media_b64: str, media_type: str) -> str:
    from app.services import ocr

    if not ocr.is_configured():
        return (
            "Photo bill feature abhi setup nahi hua hai.\n"
            "(GEMINI_API_KEY chahiye. Free milta hai aistudio.google.com se.)"
        )

    extract = await ocr.extract_bill(media_b64, media_type)
    if extract is None or not extract.readable:
        return (
            "Photo saaf nahi aayi, padh nahi paya. 🙏\n"
            "Thodi roshni mein, seedha upar se photo lekar dobara bhejein."
        )

    db = require_db()
    # One pending at a time: a new photo replaces the previous pending one
    db.table("photo_bills").update({"status": "cancelled"}).eq(
        "business_id", business["id"]).eq("status", "pending").execute()

    row = {
        "business_id": business["id"],
        "party_name": extract.party_name,
        "phone": _normalize_phone(extract.phone),
        "amount": extract.amount,
        "bill_number": extract.bill_number,
        "bill_date": extract.bill_date,
        "image_b64": media_b64,
        "image_type": media_type,
    }
    inserted = db.table("photo_bills").insert(row).execute()
    return _photo_bill_summary(inserted.data[0])


async def _correct_photo_bill(business_id: str, field: str, value: str) -> str:
    pb = await _latest_pending_photo_bill(business_id)
    if not pb:
        return "Koi photo bill pending nahi hai. Pehle bill ki photo bhejein."

    db = require_db()
    if field == "NAAM":
        db.table("photo_bills").update({"party_name": value.strip()}).eq("id", pb["id"]).execute()
        pb["party_name"] = value.strip()
    elif field == "PHONE":
        phone = _normalize_phone(value)
        if not phone:
            return "Phone number sahi nahi laga. 10 digit ka mobile number likhein, jaise: PHONE 9876543210"
        db.table("photo_bills").update({"phone": phone}).eq("id", pb["id"]).execute()
        pb["phone"] = phone
    elif field == "AMOUNT":
        try:
            amt = float(value.replace(",", "").replace("₹", "").strip())
            assert amt > 0
        except (ValueError, AssertionError):
            return "Amount samajh nahi aaya. Sirf number likhein, jaise: AMOUNT 12500"
        db.table("photo_bills").update({"amount": amt}).eq("id", pb["id"]).execute()
        pb["amount"] = amt

    return "Update ho gaya. ✅\n\n" + _photo_bill_summary(pb)


async def _cancel_photo_bill(business_id: str) -> str:
    pb = await _latest_pending_photo_bill(business_id)
    if not pb:
        return "Koi photo bill pending nahi tha."
    require_db().table("photo_bills").update({"status": "cancelled"}).eq("id", pb["id"]).execute()
    return "Theek hai, woh bill cancel kar diya."


async def _confirm_photo_bill(business: dict) -> str:
    from datetime import timedelta

    business_id = business["id"]
    pb = await _latest_pending_photo_bill(business_id)
    if not pb:
        return "Koi photo bill pending nahi hai. Pehle bill ki photo bhejein."
    if not pb.get("party_name"):
        return "Party ka naam nahi hai. Pehle bhejein: NAAM Ramesh Traders"
    if not pb.get("amount"):
        return "Amount nahi hai. Pehle bhejein: AMOUNT 12500"

    db = require_db()

    # Find or create the client
    client_resp = (
        db.table("clients").select("id, name, whatsapp_number, credit_days")
        .eq("business_id", business_id)
        .ilike("name", f"%{pb['party_name']}%")
        .execute()
    )
    if client_resp.data:
        client = client_resp.data[0]
        if pb.get("phone") and not client.get("whatsapp_number"):
            db.table("clients").update({"whatsapp_number": pb["phone"]}).eq("id", client["id"]).execute()
            client["whatsapp_number"] = pb["phone"]
    else:
        new_client = db.table("clients").insert({
            "business_id": business_id,
            "name": pb["party_name"],
            "whatsapp_number": pb.get("phone"),
        }).execute()
        client = new_client.data[0]

    # Create the bill (source=photo) - enters the reminder cadence like any bill
    invoice_date = date.fromisoformat(str(pb["bill_date"])) if pb.get("bill_date") else date.today()
    credit_days = client.get("credit_days") or 30
    invoice_number = pb.get("bill_number") or f"PH-{pb['id'][:6].upper()}"
    bill = db.table("bills").insert({
        "business_id": business_id,
        "client_id": client["id"],
        "invoice_number": invoice_number,
        "tally_voucher_number": f"PHOTO-{pb['id'][:12]}",
        "amount": pb["amount"],
        "paid_amount": 0.0,
        "invoice_date": invoice_date.isoformat(),
        "due_date": (invoice_date + timedelta(days=credit_days)).isoformat(),
        "status": "pending",
        "source": "photo",
    }).execute()

    db.table("photo_bills").update(
        {"status": "confirmed", "bill_id": bill.data[0]["id"]}
    ).eq("id", pb["id"]).execute()

    # Send the bill (original photo attached) to the customer
    sent_note = "⚠️ Phone number nahi hai, isliye customer ko message NAHI gaya."
    phone = client.get("whatsapp_number")
    if phone:
        amount_fmt = inr(Decimal(str(pb["amount"])))
        vpa = business.get("upi_vpa")
        pay_link = upi.upi_link(vpa, business.get("business_name", ""), Decimal(str(pb["amount"])), invoice_number) if vpa else ""
        body = (
            f"Namaste {client['name']} ji! 🙏\n"
            f"{business.get('business_name', '')} ki taraf se aapka bill.\n\n"
            f"Bill number: {invoice_number}\n"
            f"Amount: {amount_fmt}\n\n"
            + (f"UPI se payment: {pay_link}\n\n" if pay_link else "")
            + "Bill ki photo saath attach hai. Dhanyavaad!"
        )
        result = await whatsapp.send_message(
            business_id=business_id,
            to_number=phone,
            message_text=body,
            plan=Plan(business["plan"]),
            message_type=MessageType.invoice,
            client_id=client["id"],
            bill_id=bill.data[0]["id"],
            image_base64=pb.get("image_b64"),
            image_filename=f"bill_{invoice_number}.jpg",
            image_media_type=pb.get("image_type") or "image/jpeg",
            template_name="photo_bill_hi",
        )
        sent_note = "Customer ko bill bhej diya ✅" if result.get("sent") else "⚠️ Bhejne mein dikkat aayi, baad mein reminder jayega."

    return (
        f"Bill ban gaya ✅\n"
        f"{client['name']}: {inr(Decimal(str(pb['amount'])))} ({invoice_number})\n"
        f"{sent_note}\n"
        f"Reminders apne aap chalenge."
    )


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
        return f"{business_name}: koi outstanding nahi hai. Sab clear! 🎉"

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
    lines = [f"{business_name} ki baaki list:\n"]
    for i, (name, data) in enumerate(sorted_clients[:20], 1):
        lines.append(f"{i}. {name}: {inr(data['total'])} ({data['count']} bills)")

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
    """Instant per-party statement - the anti-'sync is broken' command.

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
        lines.append("Koi outstanding nahi, sab clear ✅")
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
            lines.append(f"• {b.get('invoice_number') or '-'}: {inr(amt)}{overdue}")
        if len(open_bills) > 8:
            rest = sum(Decimal(str(b["outstanding"])) for b in open_bills[8:])
            total += rest
            lines.append(f"• ...aur {len(open_bills) - 8} bills: {inr(rest)}")
        lines.append(f"Total baaki: {inr(total)}")

    phone = client.get("whatsapp_number")
    lines.append(f"WhatsApp: {phone if phone else '❌ number nahi hai'}")
    lines.append(f"Reminders: {'chalu' if client.get('reminders_enabled', True) else 'band'}")

    # Last sync time - proof of freshness
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


async def _open_bills_by_client(business_id: str) -> dict[str, dict]:
    """Aggregate open bills per client: {client_id: {client, bills, total, oldest_days}}."""
    db = require_db()
    resp = (
        db.table("bills")
        .select("id, invoice_number, outstanding, invoice_date, due_date, "
                "client_id, clients(id, name, whatsapp_number, reminders_enabled, language, reminder_batch)")
        .eq("business_id", business_id)
        .in_("status", ["pending", "partial", "overdue"])
        .order("invoice_date")
        .execute()
    )
    agg: dict[str, dict] = {}
    today = date.today()
    for b in resp.data or []:
        cid = b["client_id"]
        entry = agg.setdefault(cid, {
            "client": b.get("clients") or {},
            "bills": [],
            "total": Decimal(0),
            "oldest_days": 0,
        })
        entry["bills"].append(b)
        entry["total"] += Decimal(str(b["outstanding"]))
        inv_date = date.fromisoformat(str(b["invoice_date"]))
        entry["oldest_days"] = max(entry["oldest_days"], (today - inv_date).days)
    return agg


async def _send_consolidated_reminder(business: dict, entry: dict) -> tuple[bool, str]:
    """One reminder covering ALL of a party's open bills (single bill →
    bill number shown; multiple → itemised up to 4 + total). Returns
    (sent, summary-line-for-owner)."""
    client = entry["client"]
    name = client.get("name", "Customer")
    phone = client.get("whatsapp_number")
    if not phone:
        return False, f"{name}: ❌ number nahi hai"

    total = entry["total"]
    bills = entry["bills"]
    today = date.today()
    biz_name = business.get("business_name", "")
    # Professional copy in the party's reminder-batch language (Hinglish default).
    from app.services.batches import resolve_batch
    batch = resolve_batch(business, client.get("reminder_batch"))
    en = batch["lang"] == "english"

    def _age(b) -> int:
        return (today - date.fromisoformat(str(b["invoice_date"]))).days

    if en:
        lines = [f"Dear {name},", f"A payment reminder from {biz_name}.", ""]
        if len(bills) == 1:
            b = bills[0]
            lines.append(f"Invoice {b.get('invoice_number') or '-'}: {inr(total)} outstanding ({_age(b)} days).")
        else:
            lines.append(f"You have {len(bills)} pending invoices:")
            for b in bills[:4]:
                lines.append(f"- {b.get('invoice_number') or '-'}: {inr(Decimal(str(b['outstanding'])))} ({_age(b)} days)")
            if len(bills) > 4:
                lines.append(f"- and {len(bills) - 4} more")
            lines.append(f"Total outstanding: {inr(total)}")
    else:
        lines = [f"Namaste {name} ji,", f"{biz_name} ki taraf se payment ka vinamra reminder.", ""]
        if len(bills) == 1:
            b = bills[0]
            lines.append(f"Bill {b.get('invoice_number') or '-'} ka {inr(total)} baaki hai ({_age(b)} din).")
        else:
            lines.append(f"Aapke {len(bills)} bills baaki hain:")
            for b in bills[:4]:
                lines.append(f"- {b.get('invoice_number') or '-'}: {inr(Decimal(str(b['outstanding'])))} ({_age(b)} din)")
            if len(bills) > 4:
                lines.append(f"- aur {len(bills) - 4} bills")
            lines.append(f"Kul baaki: {inr(total)}")

    # Early-payment discount from the batch: QR + line reflect the discount
    # (line appears only when the batch actually sets one).
    pay_amount, discount_line = apply_discount(total, batch["disc"], batch["lang"])
    vpa = business.get("upi_vpa")
    qr_b64 = None
    if vpa:
        link = upi.upi_link(vpa, biz_name, pay_amount, f"{len(bills)} bills")
        qr_b64 = upi.qr_png_base64(link)
        lines += ["", (f"To pay via UPI: {link}" if en else f"UPI se payment: {link}")]
    if discount_line:
        lines += ["", discount_line]
    if batch.get("line"):
        lines += ["", batch["line"]]
    lines += ["", ("Once paid, kindly reply PAID. Thank you."
                   if en else "Payment ho jaye to PAID reply karein. Dhanyavaad.")]

    result = await whatsapp.send_template(
        business_id=business["id"],
        to_number=phone,
        campaign_name="manual_remind_hi",
        template_params=[name, biz_name, inr(total)],
        business_name=biz_name,
        plan=Plan(business["plan"]),
        message_type=MessageType.reminder,
        client_id=client.get("id"),
        bill_id=bills[0]["id"],
        language=Lang(client.get("language", "hi")),
        message_text="\n".join(lines),
        image_base64=qr_b64,
        image_filename="payment_qr.png",
    )
    if result.get("sent"):
        return True, f"{name}: {inr(total)} ({len(bills)} bills) bheja ✅"
    return False, f"{name}: ❌ nahi gaya ({result.get('reason') or result.get('delivery_status')})"


async def _handle_remind(business: dict, arg: str) -> str:
    """REMIND <naam> | REMIND TOP [n] | REMIND OLDEST [n] - the owner
    chooses exactly who gets nudged and in which order."""
    business_id = business["id"]
    agg = await _open_bills_by_client(business_id)
    if not agg:
        return "Koi outstanding bill nahi hai. Sab clear! 🎉"

    bulk = re.match(r"(TOP|OLDEST|OLD)\s*(\d+)?$", arg)
    if bulk:
        n = min(int(bulk.group(2) or 5), 20)
        entries = list(agg.values())
        if bulk.group(1) == "TOP":
            entries.sort(key=lambda e: e["total"], reverse=True)  # biggest first
            header = f"Top {n} outstanding walon ko reminder:"
        else:
            entries.sort(key=lambda e: e["oldest_days"], reverse=True)  # oldest first
            header = f"Sabse purane {n} ko reminder:"

        results = []
        sent_count = 0
        for entry in entries:
            if sent_count >= n:
                break
            if not entry["client"].get("reminders_enabled", True):
                continue
            if not entry["client"].get("whatsapp_number"):
                continue
            ok, line = await _send_consolidated_reminder(business, entry)
            results.append(line)
            if ok:
                sent_count += 1
        if not results:
            return "Kisi ke paas WhatsApp number nahi hai ya reminders band hain."
        return header + "\n" + "\n".join(results)

    # Single party by name
    matches = [e for e in agg.values() if arg.lower() in (e["client"].get("name") or "").lower()]
    if not matches:
        return f"'{arg}' ka koi outstanding nahi mila. CHECK {arg} se dekh sakte hain."
    if len(matches) > 1:
        names = ", ".join(e["client"].get("name", "?") for e in matches[:5])
        return f"'{arg}' se kai clients mile: {names}. Poora naam likhein."
    ok, line = await _send_consolidated_reminder(business, matches[0])
    return ("Reminder bhej diya:\n" if ok else "") + line


async def _handle_paid_owner(
    business_id: str, client_name: str, plan: Plan
) -> str:
    """Owner confirmed payment - mark paid immediately."""
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


async def _handle_terms(business_id: str, client_name: str, days: int) -> str:
    """TERMS <name> <days>: set the party's credit period. The reminder
    cadence scales with it automatically (90 din -> nudges at 9/21/45/
    63/90), and open bills' due dates are recomputed."""
    if not 1 <= days <= 365:
        return "Credit period 1 se 365 din ke beech hona chahiye."
    db = require_db()
    clients_resp = (
        db.table("clients")
        .select("id, name, credit_days")
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
    db.table("clients").update({"credit_days": days}).eq("id", client["id"]).execute()

    # Recompute due dates on open bills so the new cadence applies to them
    from datetime import timedelta
    open_resp = (
        db.table("bills")
        .select("id, invoice_date, is_opening_balance")
        .eq("business_id", business_id)
        .eq("client_id", client["id"])
        .in_("status", ["pending", "partial", "overdue"])
        .execute()
    )
    updated = 0
    for b in open_resp.data or []:
        if b.get("is_opening_balance"):
            continue  # OB bills stay due-on-FY-start
        new_due = (date.fromisoformat(str(b["invoice_date"])) + timedelta(days=days)).isoformat()
        db.table("bills").update({"due_date": new_due}).eq("id", b["id"]).execute()
        updated += 1

    return (
        f"{client['name']} ka credit period ab {days} din hai. ✅\n"
        f"{updated} khule bills ki due date update ho gayi.\n"
        f"Reminders ab {days} din ke hisaab se jayenge."
    )


async def _customer_statement(client: dict) -> str:
    """A customer asked HISAB: their own bills, personalised to their number."""
    db = require_db()
    bills_resp = (
        db.table("bills")
        .select("invoice_number, outstanding, invoice_date, status")
        .eq("business_id", client["business_id"])
        .eq("client_id", client["id"])
        .in_("status", ["pending", "partial", "overdue"])
        .order("invoice_date")
        .execute()
    )
    open_bills = bills_resp.data or []
    if not open_bills:
        return (
            f"Namaste {client['name']} ji! 🙏\n"
            "Aapka koi bill baaki nahi hai. Sab clear! ✅\n"
            "Dhanyavaad."
        )

    total = Decimal(0)
    lines = [f"Namaste {client['name']} ji! Aapka hisaab:\n"]
    for b in open_bills[:6]:
        amt = Decimal(str(b["outstanding"]))
        total += amt
        d = date.fromisoformat(str(b["invoice_date"])).strftime("%d-%m-%Y")
        lines.append(f"Bill {b.get('invoice_number') or '-'}: {inr(amt)} ({d})")
    if len(open_bills) > 6:
        rest = sum(Decimal(str(b["outstanding"])) for b in open_bills[6:])
        total += rest
        lines.append(f"...aur {len(open_bills) - 6} bills: {inr(rest)}")
    lines.append(f"\nKul baaki: {inr(total)}")
    lines.append("\nPayment ho gaya ho to PAID reply karein. Dhanyavaad! 🙏")
    return "\n".join(lines)


async def _handle_paid_customer(client: dict) -> str:
    """Customer claimed payment - do NOT auto-mark. Notify owner to confirm.

    Security: anyone who knows the WhatsApp number could send PAID.
    Only the owner can actually mark bills as paid.
    """
    db = require_db()
    business_id = client["business_id"]

    # Notify owner on the company number. Tally is the source of truth now, so
    # nudge them to record it there - the next sync updates the dashboard/amount
    # automatically (works for partial payments too). The quick bot command
    # still exists as a fallback.
    await whatsapp.notify_owner(
        business_id,
        f"{client['name']} ne 'PAID' bola hai. Paisa aaya ho to Tally me receipt "
        f"entry kar dein. Dashboard sync se apne aap update ho jayega (partial "
        f"payment bhi sahi dikhega). Ya yahan 'PAID {client['name']}' bhej ke turant mark karein.",
    )

    return (
        "Shukriya! Aapka payment note kar liya hai. "
        "Business owner ko inform kar diya gaya hai. 🙏"
    )
