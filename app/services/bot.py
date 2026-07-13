"""WhatsApp bot command parser.

Handles inbound messages from business owners and their customers.
Commands are parsed with regex first. Gemini fallback is Phase 3.

# Owner-facing bot replies are simple English (20-70 age audience).
# Customer-facing messages follow the business/batch language.

Security rule (from CTO audit):
  - PAID from owner number → mark paid immediately
  - PAID from customer number → notify owner to confirm, do NOT auto-mark
"""
from __future__ import annotations

import asyncio
import logging
import random
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
        return "Noted. The ASVA team will contact you soon."
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
    return "Your message has been sent to the ASVA team.\nYou will get a reply soon."

log = logging.getLogger(__name__)

# Greeting / help keywords that should ALWAYS get a reply, even from a number
# we do not recognise (so a new customer or a pilot tester is never ghosted).
_GREETING = ("HI", "HELLO", "HELP", "MENU", "START", "?", "HEY", "NAMASTE")


def _last10(n: str) -> str:
    """Last 10 digits of a phone number - used as a format-agnostic match key
    so a number stored as 91XXXXXXXXXX still matches +91, 0-prefixed, etc."""
    d = "".join(c for c in str(n or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _send_fail_note(result: dict) -> str:
    """Turn a whatsapp.send_message failure into a line the owner can act on.
    Never let a send fail silently - the owner must know it did NOT go."""
    reason = result.get("reason") or result.get("delivery_status") or "unknown"
    return {
        "wa_not_connected": "WhatsApp is not connected. Open ASVA and scan the QR. Message NOT sent.",
        "wa_service_down": "ASVA is not running on the shop laptop. Start it first. Message NOT sent.",
        "not_on_whatsapp": "This number is not on WhatsApp. Message NOT sent.",
        "limit_reached": "This month's message limit is over. Message NOT sent.",
        "subscription_suspended": "Your subscription has expired. Renew to send. Message NOT sent.",
    }.get(reason, f"Message NOT sent ({reason}).")


def _match_row(db, table: str, select: str, from_number: str):
    """Find a row in `table` whose whatsapp_number matches `from_number`.
    Exact match first, then a last-10-digit fallback (format drift safety).
    Ordered by created_at so a multi-company owner always resolves to the
    OLDEST (primary) company deterministically."""
    r = (db.table(table).select(select).eq("whatsapp_number", from_number)
         .order("created_at").limit(1).execute())
    if r.data:
        return r.data[0]
    last10 = _last10(from_number)
    if last10:
        r = (db.table(table).select(select).like("whatsapp_number", f"%{last10}")
             .order("created_at").limit(1).execute())
        if r.data:
            return r.data[0]
    return None


async def handle(
    from_number: str,
    text: str,
    media_b64: str | None = None,
    media_type: str = "image/jpeg",
    channel: str = "shop",
) -> str:
    """Route an inbound WhatsApp message to the right handler.

    Args:
        from_number: Sender's WhatsApp number (E.164 without +, e.g. 919876543210).
        text: Message body, already stripped.
        channel: Which number received this - "shop" (customer-facing) or
            "bot" (the ASVA assistant number, which is strictly owner-only).

    Returns:
        Reply text to send back (via AiSensy or log in dev mode).
    """
    db = require_db()
    upper = text.upper().strip()

    # ── Identify sender: owner or customer? ───────────────────────────
    business = _match_row(
        db, "businesses",
        "id, business_name, plan, whatsapp_number, upi_vpa, discount_pct, "
        "msg_language, reminder_batches, reminder_hour",
        from_number)
    is_owner = business is not None

    # ── Bot number is OWNER-ONLY ──────────────────────────────────────
    # The ASVA assistant number serves registered shop owners only. If a
    # non-owner (a customer or a stranger) messages it, we never run the
    # customer self-service flow here - we reply to a greeting once so they
    # are not ghosted, and stay silent otherwise.
    if channel == "bot" and not is_owner:
        if upper in _GREETING:
            team = settings.product_team_number
            tail = f"\n\nTo join ASVA, contact: {team}" if team else ""
            return (
                "Hello! This is the ASVA assistant number.\n"
                "It works only for registered ASVA shop owners." + tail
            )
        log.info("Non-owner %s messaged the bot channel: %s", from_number, text)
        return ""

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

        # ── DIGEST - today's summary, on demand (same as the nightly one) ─
        if upper in ("DIGEST", "REPORT", "SUMMARY", "AAJ"):
            return await _handle_digest(business_id)

        # ── DIGEST 9PM / DIGEST TIME 21 - set the daily summary time ──
        dt_match = re.match(r"DIGEST(?:\s+TIME)?\s+(\d{1,2})\s*(AM|PM)?$", upper)
        if dt_match:
            return await _set_digest_time(
                business_id, int(dt_match.group(1)), dt_match.group(2))

        # ── MSG <party>: <text> - free-form message to one party ──────
        msg_match = re.match(r"(?:MSG|MESSAGE|SEND)\s+(.+)", text.strip(),
                             re.IGNORECASE | re.DOTALL)
        if msg_match:
            return await _handle_owner_msg(business, msg_match.group(1).strip())

        # ── BILL <party> <amount> [phone] - add a non-Tally bill by text ─
        bill_match = re.match(r"BILL\s+(.+)", text.strip(), re.IGNORECASE)
        if bill_match:
            return await _handle_text_bill(business, bill_match.group(1).strip())

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
        # For 20-70 year old shop owners: 7 commands, one simple example each,
        # clear separators, plain words. "Ramesh" is only an example name.
        prefix = ("" if upper in ("HELP", "MENU", "?", "HI", "HELLO", "START")
                  else "Sorry, I did not understand that.\nHere is what I can do:\n\n")
        line = "------------------------"
        return (
            prefix
            + "ASVA - your collection helper.\n"
            "Type a command with your party's name.\n"
            "(Below \"Ramesh\" is only an example.)\n\n"
            f"{line}\n"
            "LIST\n"
            "See everyone who owes you.\n"
            f"{line}\n"
            "CHECK Ramesh\n"
            "See one party's balance.\n"
            f"{line}\n"
            "REMIND Ramesh\n"
            "Send a payment reminder now.\n"
            f"{line}\n"
            "BILL Ramesh 12500\n"
            "Add a new bill. Or send a photo of it.\n"
            f"{line}\n"
            "PAID Ramesh\n"
            "Mark a payment as received.\n"
            f"{line}\n"
            "STOP Ramesh\n"
            "Stop reminders. START to resume.\n"
            f"{line}\n"
            "DIGEST\n"
            "Get today's summary now.\n"
            f"{line}\n\n"
            "Need help? Type: TEAM your message"
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
                "HISAB bhejein - apna baaki dekhein\n"
                "PAID bhejein - payment ki khabar dein\n\n"
                "Aapka number hamare records me nahi mila.\n"
                "Dukaan se baat karni ho to TEAM likhkar apni baat bhejein."
            )
        log.info("Message from unknown number %s: %s", from_number, text)
        return ""  # stay silent on non-greeting messages from unknown numbers

    # ── Customer opt-out (silent safety net; NOT advertised in any message) ─
    # We never tell customers to "reply STOP" - it would clutter the message and
    # invite opt-outs. But if a customer DOES ask to stop, we honour it at once:
    # pause their reminders and tell the owner. Protects the WhatsApp number.
    low = text.lower()
    if upper in ("STOP", "UNSUBSCRIBE", "OPTOUT") or any(
        p in low for p in ("band kar", "mat bhej", "reminder band", "stop reminder", "unsubscribe")
    ):
        return await _handle_customer_optout(client, from_number)

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
        return ("Your message has been passed to the shop. You will get a reply soon."
                if _biz_is_en(client["business_id"])
                else "Aapki baat dukaan tak pahuncha di. Jaldi jawab milega.")

    if upper in ("MENU", "HELP", "?", "HI", "HELLO"):
        if _biz_is_en(client["business_id"]):
            return (
                f"Hello {client['name']} ji,\n"
                "I am this shop's assistant.\n\n"
                "HISAB - see your full balance\n"
                "PAID - tell us you have paid\n\n"
                "Any question? Type TEAM and your message.\n"
                "We will pass it to the shop."
            )
        return (
            f"Namaste {client['name']} ji,\n"
            "Main is dukaan ka assistant hoon.\n\n"
            "HISAB - apna poora baaki dekhein\n"
            "PAID - payment ki khabar dein\n\n"
            "Koi sawaal ho to TEAM likhkar apni baat bhejein.\n"
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


def _amount_token(tok: str) -> Decimal | None:
    """Parse an amount token ('12500', '1,73,632', '₹500'). None if not one."""
    try:
        a = Decimal(str(tok).replace(",", "").replace("₹", ""))
        return a if a > 0 else None
    except Exception:
        return None


def _parse_text_bill(rest: str) -> tuple[str, Decimal, str | None, int | None] | None:
    """Parse a typed bill: 'Ramesh Traders 12500 9876543210'.

    Returns (party_name, amount, phone_or_None, credit_days_or_None).
    Reads right-to-left, in any trailing order:
      - an optional 10/12-digit phone,
      - an optional credit period 1-365 ('45', '45D', '45DIN', '45DAYS') -
        only taken when ANOTHER number (the amount) still sits before it, so
        'BILL Ramesh 300' stays amount=300, never days=300,
      - then the amount (last remaining number); the party name is the rest.
    Returns None if it can't find a valid name + positive amount.
    """
    tokens = rest.split()
    if len(tokens) < 2:
        return None
    phone: str | None = None
    days: int | None = None
    changed = True
    while changed and len(tokens) >= 3:
        changed = False
        t = tokens[-1]
        if phone is None and _normalize_phone(t):
            phone = _normalize_phone(t)
            tokens.pop()
            changed = True
            continue
        m = re.fullmatch(r"(\d{1,3})(?:D|DIN|DAYS?)?", t.upper())
        if (days is None and m and 1 <= int(m.group(1)) <= 365
                and _amount_token(tokens[-2]) is not None):
            days = int(m.group(1))
            tokens.pop()
            changed = True
    if len(tokens) < 2:
        return None
    amount = _amount_token(tokens[-1])
    if amount is None:
        return None
    name = " ".join(tokens[:-1]).strip()
    if not name:
        return None
    return name, amount, phone, days


def _photo_bill_summary(pb: dict) -> str:
    amount = inr(Decimal(str(pb["amount"]))) if pb.get("amount") else "not found"
    lines = [
        "Read from the photo:",
        "",
        f"Party: {pb.get('party_name') or 'not found'}",
        f"Phone: {pb.get('phone') or 'not found'}",
        f"Amount: {amount}",
    ]
    if pb.get("bill_number"):
        lines.append(f"Bill no: {pb['bill_number']}")
    lines += [
        "",
        "All correct? Send YES.",
        "",
        "To fix a mistake, send one of these:",
        "NAAM Ramesh Traders",
        "PHONE 9876543210",
        "AMOUNT 12500",
        "",
        "To drop it, send CANCEL.",
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
            "Photo bills are not set up yet.\n"
            "(Needs GEMINI_API_KEY - free at aistudio.google.com.)"
        )

    extract = await ocr.extract_bill(media_b64, media_type)
    if extract is None or not extract.readable:
        return (
            "Could not read the photo clearly.\n\n"
            "Please take it again in good light,\n"
            "straight from above, and send once more."
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
        return "No photo bill is waiting. Send the bill photo first."

    db = require_db()
    if field == "NAAM":
        db.table("photo_bills").update({"party_name": value.strip()}).eq("id", pb["id"]).execute()
        pb["party_name"] = value.strip()
    elif field == "PHONE":
        phone = _normalize_phone(value)
        if not phone:
            return "That phone number does not look right.\nSend a 10 digit mobile number, like:\nPHONE 9876543210"
        db.table("photo_bills").update({"phone": phone}).eq("id", pb["id"]).execute()
        pb["phone"] = phone
    elif field == "AMOUNT":
        try:
            amt = float(value.replace(",", "").replace("₹", "").strip())
            assert amt > 0
        except (ValueError, AssertionError):
            return "Could not read that amount.\nSend numbers only, like:\nAMOUNT 12500"
        db.table("photo_bills").update({"amount": amt}).eq("id", pb["id"]).execute()
        pb["amount"] = amt

    return "Updated.\n\n" + _photo_bill_summary(pb)


async def _cancel_photo_bill(business_id: str) -> str:
    pb = await _latest_pending_photo_bill(business_id)
    if not pb:
        return "No photo bill was waiting."
    require_db().table("photo_bills").update({"status": "cancelled"}).eq("id", pb["id"]).execute()
    return "OK, that bill is cancelled."


async def _confirm_photo_bill(business: dict) -> str:
    from datetime import timedelta

    business_id = business["id"]
    pb = await _latest_pending_photo_bill(business_id)
    if not pb:
        return "No photo bill is waiting. Send the bill photo first."
    if not pb.get("party_name"):
        return "The party name is missing. First send:\nNAAM Ramesh Traders"
    if not pb.get("amount"):
        return "The amount is missing. First send:\nAMOUNT 12500"

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
    sent_note = "No phone number, so the customer did NOT get the bill."
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
        sent_note = ("Bill sent to the customer." if result.get("sent")
                     else "Could not send it right now. Reminders will still go automatically.")

    return (
        f"Bill created.\n\n"
        f"{client['name']}: {inr(Decimal(str(pb['amount'])))} ({invoice_number})\n"
        f"{sent_note}\n\n"
        f"Reminders will run automatically."
    )


async def _handle_text_bill(business: dict, rest: str) -> str:
    """Owner adds a non-Tally bill by text: BILL <party> <amount> [phone].

    Unlike the photo path there is no confirm step - the typed line is already
    structured - so the bill is created directly, enters the reminder cadence
    like any bill, and is sent to the customer if a phone number is on file.
    """
    from datetime import timedelta
    import uuid

    parsed = _parse_text_bill(rest)
    if not parsed:
        return (
            "To add a bill, type it like this:\n\n"
            "BILL Ramesh Traders 12500\n"
            "BILL Ramesh Traders 12500 45 (45 days credit)\n"
            "BILL Ramesh Traders 12500 9876543210 (with phone)\n\n"
            "First the party name, then the amount."
        )
    party_name, amount, phone, days = parsed
    business_id = business["id"]
    db = require_db()

    # Find or create the client (same fuzzy match as the photo path)
    client_resp = (
        db.table("clients").select("id, name, whatsapp_number, credit_days")
        .eq("business_id", business_id)
        .ilike("name", f"%{party_name}%")
        .execute()
    )
    if client_resp.data:
        client = client_resp.data[0]
        if phone and not client.get("whatsapp_number"):
            db.table("clients").update({"whatsapp_number": phone}).eq("id", client["id"]).execute()
            client["whatsapp_number"] = phone
    else:
        client = db.table("clients").insert({
            "business_id": business_id,
            "name": party_name,
            "whatsapp_number": phone,
        }).execute().data[0]

    invoice_date = date.today()
    # Priority: days typed in THIS command > the party's saved credit_days > 30.
    credit_days = days or client.get("credit_days") or 30
    due_date = invoice_date + timedelta(days=credit_days)
    invoice_number = f"TX-{uuid.uuid4().hex[:6].upper()}"
    bill = db.table("bills").insert({
        "business_id": business_id,
        "client_id": client["id"],
        "invoice_number": invoice_number,
        "tally_voucher_number": f"TEXT-{uuid.uuid4().hex[:12]}",
        "amount": float(amount),
        "paid_amount": 0.0,
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "status": "pending",
        # 'manual' = typed by the owner (bills_source_check allows only
        # tally/photo/manual - 'text' violates the constraint).
        "source": "manual",
    }).execute()

    # Send the bill to the customer if we have a number
    amount_fmt = inr(amount)
    phone = client.get("whatsapp_number")
    if phone:
        vpa = business.get("upi_vpa")
        pay_link = upi.upi_link(vpa, business.get("business_name", ""), amount, invoice_number) if vpa else ""
        body = (
            f"Namaste {client['name']} ji! 🙏\n"
            f"{business.get('business_name', '')} ki taraf se aapka bill.\n\n"
            f"Bill number: {invoice_number}\n"
            f"Amount: {amount_fmt}\n\n"
            + (f"UPI se payment: {pay_link}\n\n" if pay_link else "")
            + "Dhanyavaad!"
        )
        result = await whatsapp.send_message(
            business_id=business_id,
            to_number=phone,
            message_text=body,
            plan=Plan(business["plan"]),
            message_type=MessageType.invoice,
            client_id=client["id"],
            bill_id=bill.data[0]["id"],
        )
        if result.get("queued"):
            sent_note = "Bill is in the queue. It will reach the customer from your shop number."
        elif result.get("sent"):
            sent_note = "Bill sent to the customer."
        else:
            sent_note = _send_fail_note(result)
    else:
        sent_note = "No phone number, so the customer did NOT get the bill."

    return (
        f"Bill created.\n\n"
        f"{client['name']}: {amount_fmt} ({invoice_number})\n"
        f"Due: {due_date.strftime('%d-%m-%Y')} ({credit_days} days credit)\n"
        f"{sent_note}\n\n"
        f"Reminders will run automatically."
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
        return f"{business_name}: nothing pending. All clear!"

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
    lines = [f"{business_name} - who owes you:\n"]
    for i, (name, data) in enumerate(sorted_clients[:20], 1):
        lines.append(f"{i}. {name}: {inr(data['total'])} ({data['count']} bills)")

    if len(sorted_clients) > 20:
        lines.append(f"\n...and {len(sorted_clients) - 20} more")

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
        return f"'{client_name}' - no party found with that name. Type the exact name."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' matches more than one party: {names}. Type the full name."

    client = clients_resp.data[0]
    if not client["reminders_enabled"]:
        return f"{client['name']}'s reminders are already off."

    db.table("clients").update({"reminders_enabled": False}).eq(
        "id", client["id"]
    ).execute()

    return (f"{client['name']}'s reminders are now OFF.\n"
            f"To start again, send: START {client['name']}")


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
        return f"'{client_name}' - no party found with that name. Type the exact name."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' matches more than one party: {names}. Type the full name."

    client = clients_resp.data[0]
    if client["reminders_enabled"]:
        return f"{client['name']}'s reminders are already on."

    # Selection day = today: the overdue track restarts from the day the
    # owner switches a party ON (see reminder_anchor in the sweep).
    db.table("clients").update(
        {"reminders_enabled": True, "reminder_anchor": date.today().isoformat()}).eq(
        "id", client["id"]
    ).execute()

    return f"{client['name']}'s reminders are now ON."


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
        return f"'{client_name}' - no party found with that name. Type the exact name."
    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' matches more than one party: {names}. Type the full name."

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
        lines.append("Nothing pending. All clear.")
    else:
        total = Decimal(0)
        for b in open_bills[:8]:
            amt = Decimal(str(b["outstanding"]))
            total += amt
            overdue = ""
            if b.get("due_date"):
                days = (_date.today() - _date.fromisoformat(str(b["due_date"]))).days
                if days > 0:
                    overdue = f" ({days} days late)"
            lines.append(f"• {b.get('invoice_number') or '-'}: {inr(amt)}{overdue}")
        if len(open_bills) > 8:
            rest = sum(Decimal(str(b["outstanding"])) for b in open_bills[8:])
            total += rest
            lines.append(f"• ...and {len(open_bills) - 8} more bills: {inr(rest)}")
        lines.append(f"Total due: {inr(total)}")

    phone = client.get("whatsapp_number")
    lines.append(f"WhatsApp: {phone if phone else 'number missing'}")
    lines.append(f"Reminders: {'ON' if client.get('reminders_enabled', True) else 'OFF'}")

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
        lines.append(f"Last Tally sync: {str(sync_resp.data[0]['synced_at'])[:16]}")
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
        return False, f"{name}: no phone number"

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
    from app.services.batches import batch_vpa
    vpa = batch_vpa(business, batch)
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
        language=Lang(client.get("language") or "hi"),
        message_text="\n".join(lines),
        image_base64=qr_b64,
        image_filename="payment_qr.png",
    )
    if result.get("queued"):
        return True, f"{name}: {inr(total)} ({len(bills)} bills) - in queue, goes from your shop number"
    if result.get("sent"):
        return True, f"{name}: {inr(total)} ({len(bills)} bills) - sent"
    return False, f"{name}: {_send_fail_note(result)}"


async def _bulk_remind(business: dict, entries: list[dict], n: int, header: str) -> str:
    """Send up to n consolidated reminders with human-like pacing between
    sends (bursts are the main WhatsApp ban trigger). Shared by REMIND
    TOP/OLDEST/BATCH. Reports every party's outcome, including failures."""
    results = []
    sent_count = 0
    for entry in entries:
        if sent_count >= n:
            break
        if not entry["client"].get("reminders_enabled", True):
            continue
        if not entry["client"].get("whatsapp_number"):
            continue
        # Only pause between actual sends, not before the first.
        if sent_count > 0:
            await asyncio.sleep(random.uniform(settings.send_gap_min_s, settings.send_gap_max_s))
        ok, line = await _send_consolidated_reminder(business, entry)
        results.append(line)
        if ok:
            sent_count += 1
    if not results:
        return "No one to remind. Parties need a WhatsApp number and reminders ON."
    return header + "\n" + "\n".join(results)


def _batch_label(i: int, b: dict) -> str:
    lang = "English" if b["lang"] == "english" else "Hindi"
    return f"{i + 1}. {b['name']} ({lang}, {b['hour']:02d}:00)"


async def _handle_remind(business: dict, arg: str) -> str:
    """REMIND <naam> | REMIND TOP [n] | REMIND OLDEST [n] | REMIND BATCH 1[,2]
    - the owner chooses exactly who gets nudged and in which order."""
    from app.services.batches import get_batches

    business_id = business["id"]
    agg = await _open_bills_by_client(business_id)
    if not agg:
        return "No pending bills. All clear!"

    # ── REMIND BATCH 1  /  REMIND BATCH 1,2 ──────────────────────────
    bmatch = re.match(r"BATCH(?:ES)?\s*([\d,\s]+)$", arg)
    if bmatch:
        batches = get_batches(business)
        try:
            want = sorted({int(x) for x in re.split(r"[\s,]+", bmatch.group(1).strip()) if x})
        except ValueError:
            want = []
        if not want or any(i < 1 or i > len(batches) for i in want):
            listing = "\n".join(_batch_label(i, b) for i, b in enumerate(batches))
            return ("That batch number is not right. Type it like:\n"
                    "REMIND BATCH 1 or REMIND BATCH 1,2\n\nYour batches:\n" + listing)
        idxs = {i - 1 for i in want}
        entries = [e for e in agg.values()
                   if int(e["client"].get("reminder_batch") or 0) in idxs]
        if not entries:
            return ("Batch " + ",".join(map(str, want)) +
                    " has no pending parties.")
        entries.sort(key=lambda e: e["total"], reverse=True)
        cap = settings.daily_reminder_cap
        header = "Batch " + ",".join(map(str, want)) + (
            f" - reminders to {len(entries)} parties:" if len(entries) <= cap
            else f" - {len(entries)} parties, first {cap} today:")
        return await _bulk_remind(business, entries, min(len(entries), cap), header)

    # ── REMIND TOP [n] / REMIND OLDEST [n] ───────────────────────────
    bulk = re.match(r"(TOP|OLDEST|OLD)\s*(\d+)?$", arg)
    if bulk:
        n = min(int(bulk.group(2) or 5), 20)
        entries = list(agg.values())
        if bulk.group(1) == "TOP":
            entries.sort(key=lambda e: e["total"], reverse=True)  # biggest first
            header = f"Reminders to your top {n} dues:"
        else:
            entries.sort(key=lambda e: e["oldest_days"], reverse=True)  # oldest first
            header = f"Reminders to the {n} oldest dues:"
        return await _bulk_remind(business, entries, n, header)

    # Single party by name
    matches = [e for e in agg.values() if arg.lower() in (e["client"].get("name") or "").lower()]
    if not matches:
        return f"No pending bills for '{arg}'. Try: CHECK {arg}"
    if len(matches) > 1:
        names = ", ".join(e["client"].get("name", "?") for e in matches[:5])
        return f"'{arg}' matches more than one party: {names}. Type the full name."
    ok, line = await _send_consolidated_reminder(business, matches[0])
    return ("Reminder sent:\n" if ok else "") + line


def _fmt_hour12(h: int) -> str:
    """23 -> '11 PM', 9 -> '9 AM', 0 -> '12 AM'."""
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {ampm}"


async def _set_digest_time(business_id: str, hour: int, ampm: str | None) -> str:
    """DIGEST 9PM - the owner sets their daily summary time from WhatsApp."""
    if ampm == "PM" and hour < 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23:
        return ("That time did not work.\n\n"
                "Type it like this: DIGEST 9PM")
    require_db().table("businesses").update(
        {"digest_hour": hour}).eq("id", business_id).execute()
    return (f"Done.\n\n"
            f"Your daily summary will now come at {_fmt_hour12(hour)} every day.")


async def _handle_digest(business_id: str) -> str:
    """DIGEST - today's summary on demand (same numbers as the nightly one).
    Comes back as the bot's reply, so it works even if outbound sends are
    down."""
    from app.jobs import eod_digest
    try:
        p = await eod_digest.preview(business_id)
    except Exception:
        log.exception("On-demand digest failed for %s", business_id)
        return "Could not build the summary. Please send DIGEST again in a minute."
    if not p.get("would_send"):
        return "Today's summary: no new bills and no payments. All quiet."
    return p.get("rendered_message") or "The summary came back empty. Please try again in a minute."


async def _handle_owner_msg(business: dict, rest: str) -> str:
    """MSG <party>: <text> - owner sends a free-form WhatsApp message to one
    party through ASVA. Colon separates name from message; without a colon
    the first word is taken as the name."""
    if ":" in rest:
        name_part, msg_part = rest.split(":", 1)
    else:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return ("To send a message, type it like this:\n\n"
                    "MSG Ramesh Traders: your goods are ready\n\n"
                    "(Name, then a colon, then your message.)")
        name_part, msg_part = parts
    name_part = name_part.strip()
    msg_part = msg_part.strip()
    if not name_part or not msg_part:
        return ("To send a message, type it like this:\n\n"
                "MSG Ramesh Traders: your goods are ready")

    db = require_db()
    matches = (
        db.table("clients")
        .select("id, name, whatsapp_number")
        .eq("business_id", business["id"])
        .ilike("name", f"%{name_part}%")
        .limit(6)
        .execute()
    ).data or []
    if not matches:
        return f"'{name_part}' - no party found with that name."
    if len(matches) > 1:
        names = ", ".join(c["name"] for c in matches[:5])
        return (f"'{name_part}' se kai clients mile: {names}.\n"
                f"Type the full name, like: MSG {matches[0]['name']}: your message")
    client = matches[0]
    if not client.get("whatsapp_number"):
        return f"{client['name']} has no WhatsApp number saved. Cannot send."

    result = await whatsapp.send_message(
        business_id=business["id"],
        to_number=client["whatsapp_number"],
        message_text=msg_part,
        plan=Plan(business["plan"]),
        message_type=MessageType.bot_reply,
        client_id=client["id"],
    )
    if result.get("queued"):
        return f"Message for {client['name']} is in the queue. It will go from your shop number."
    if result.get("sent"):
        return f"Message sent to {client['name']}."
    return f"{client['name']}: {_send_fail_note(result)}"


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
        return f"'{client_name}' - no party found with that name."

    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' matches more than one party: {names}. Type the full name."

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
        return f"{client['name']} has no pending bill."

    outstanding = Decimal(str(bill_resp.data[0]["outstanding"]))
    result = await payments_service.apply_payment(
        business_id=business_id,
        client_id=client["id"],
        amount=outstanding,
        source="bot",
    )

    if result.get("applied"):
        return (
            f"Payment of {inr(outstanding)} marked for {client['name']}.\n"
            f"{result['bills_affected']} bill(s) updated."
        )
    return f"Could not apply the payment: {result.get('reason', 'unknown error')}"


async def _handle_terms(business_id: str, client_name: str, days: int) -> str:
    """TERMS <name> <days>: set the party's credit period. The reminder
    cadence scales with it automatically (90 din -> nudges at 9/21/45/
    63/90), and open bills' due dates are recomputed."""
    if not 1 <= days <= 365:
        return "Credit days must be between 1 and 365."
    db = require_db()
    clients_resp = (
        db.table("clients")
        .select("id, name, credit_days")
        .eq("business_id", business_id)
        .ilike("name", f"%{client_name}%")
        .execute()
    )
    if not clients_resp.data:
        return f"'{client_name}' - no party found with that name. Type the exact name."
    if len(clients_resp.data) > 1:
        names = ", ".join(c["name"] for c in clients_resp.data[:5])
        return f"'{client_name}' matches more than one party: {names}. Type the full name."

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
        f"{client['name']}'s credit period is now {days} days.\n"
        f"{updated} open bills got a new due date.\n"
        f"Reminders will follow the new period."
    )


def _biz_is_en(business_id: str) -> bool:
    """Does this business message its customers in English?"""
    try:
        r = (require_db().table("businesses").select("msg_language")
             .eq("id", business_id).limit(1).execute())
        return bool(r.data) and (r.data[0].get("msg_language") or "") == "english"
    except Exception:
        return False


async def _customer_statement(client: dict) -> str:
    """A customer asked HISAB: their own bills, in the shop's message language.
    Short lines, generous spacing - readable at any age."""
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
    en = _biz_is_en(client["business_id"])
    if not open_bills:
        if en:
            return (f"Hello {client['name']} ji,\n\n"
                    "You have no pending bills.\n"
                    "All clear. Thank you!")
        return (f"Namaste {client['name']} ji,\n\n"
                "Aapka koi bill baaki nahi hai.\n"
                "Sab clear. Dhanyavaad!")

    total = Decimal(0)
    lines = ([f"Hello {client['name']} ji,", "Your account with us:", ""]
             if en else
             [f"Namaste {client['name']} ji,", "Aapka hisaab:", ""])
    for b in open_bills[:6]:
        amt = Decimal(str(b["outstanding"]))
        total += amt
        d = date.fromisoformat(str(b["invoice_date"])).strftime("%d-%m-%Y")
        lines.append(f"Bill {b.get('invoice_number') or '-'}: {inr(amt)} ({d})")
    if len(open_bills) > 6:
        rest = sum(Decimal(str(b["outstanding"])) for b in open_bills[6:])
        total += rest
        lines.append((f"...and {len(open_bills) - 6} more bills: {inr(rest)}") if en
                     else (f"...aur {len(open_bills) - 6} bills: {inr(rest)}"))
    if en:
        lines += ["", f"Total due: {inr(total)}", "",
                  "If you have already paid, reply PAID.", "Thank you!"]
    else:
        lines += ["", f"Kul baaki: {inr(total)}", "",
                  "Payment ho gaya ho to PAID reply karein.", "Dhanyavaad!"]
    return "\n".join(lines)


async def _handle_customer_optout(client: dict, from_number: str) -> str:
    """A customer asked to stop reminders. Pause them and notify the owner.
    Not advertised anywhere - this only fires if the customer initiates it."""
    db = require_db()
    try:
        db.table("clients").update({"reminders_enabled": False}).eq("id", client["id"]).execute()
    except Exception:
        log.exception("opt-out pause failed for %s", client.get("name"))
    try:
        await whatsapp.notify_owner(
            client["business_id"],
            f"{client['name']} ({from_number}) asked to stop reminders. "
            f"ASVA has paused them. To resume, tick the party on the Dashboard.")
    except Exception:
        log.exception("opt-out owner notify failed")
    if _biz_is_en(client["business_id"]):
        return ("OK. We will not send you payment reminders now.\n"
                "You can always talk to the shop directly. Thank you.")
    return ("Theek hai, aapko ab payment reminder nahi bhejenge.\n"
            "Zaroorat ho to dukaan se baat kar sakte hain. Dhanyavaad.")


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
        f"{client['name']} says PAID. If the money has come, enter the receipt "
        f"in Tally - ASVA updates automatically. Or send PAID {client['name']} "
        f"here to mark it right now.",
    )

    if _biz_is_en(business_id):
        return ("Thank you! We have noted your payment.\n"
                "The shop has been informed.")
    return ("Shukriya! Aapka payment note kar liya hai.\n"
            "Dukaan ko inform kar diya hai.")
