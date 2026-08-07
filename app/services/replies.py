"""Reply capture pipeline: turn a customer's WhatsApp reply into an action.

One entry point, capture_reply(), called from the customer branch of the bot.

DESIGN (owner-in-the-loop): ASVA NEVER replies to the customer on the owner's
behalf. A shop's customer relationship is the owner's - an auto-reply in the
owner's voice can backfire (wrong tone, wrong facts, "why is a bot answering
me?"). So capture_reply always returns None (nothing is sent to the customer)
and instead NUDGES THE OWNER: what the customer said, what ASVA did about it,
and how to override. The owner decides whether and how to reply to the customer.

What ASVA *does* do on its own is INTERNAL and reversible only: pause that
party's reminders (with a grace window that auto-resumes, capped at
promise_max_hold_days). Pausing is invisible to the customer and safe; replying
is not, so we don't.

Order of understanding:
  1. keyword fast-path  (PAID / PAID <amount>) - zero cost, high trust
  2. a screenshot       - forwarded to the owner as proof, paused on grace
  3. Gemini classify    - for natural, multilingual free text
  4. confidence gate    - dispute / unclear / low confidence -> owner decides, no pause
Always returns None: the bot sends the customer nothing. The owner is nudged.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import conversations, intent, names, promises, whatsapp
from app.services import receipts_queue as rq
from app.services.templates import inr

log = logging.getLogger(__name__)
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

_PAID_RE = re.compile(r"^\s*paid\b(.*)$", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(\d[\d,]*)\s*(k|hazaar|hazar|thousand)?", re.IGNORECASE)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _extract_amount(s: str):
    """Pull a rupee amount out of a short phrase: '20000'->20000, '20k'->20000."""
    m = _AMOUNT_RE.search(s or "")
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):                      # k / thousand
        n *= 1000
    return n if n > 0 else None


def _cap(dt: _dt.datetime) -> _dt.datetime:
    """No hold may silence reminders longer than promise_max_hold_days."""
    ceiling = _now() + _dt.timedelta(days=settings.promise_max_hold_days)
    return min(dt, ceiling)


def _grace_hold() -> _dt.datetime:
    return _cap(_now() + _dt.timedelta(days=max(1, settings.promise_grace_days)))


def _promise_hold(promise_date: str) -> _dt.datetime:
    """Hold until the end of the promised day (IST), floored to at least today's
    grace and capped at the max hold."""
    try:
        d = _dt.date.fromisoformat(str(promise_date)[:10])
    except (TypeError, ValueError):
        return _grace_hold()
    end_ist = _dt.datetime.combine(d, _dt.time(23, 59), tzinfo=IST)
    return _cap(max(end_ist.astimezone(_dt.timezone.utc), _grace_hold()))


async def _notify_owner(business_id: str, text: str) -> None:
    try:
        await whatsapp.notify_owner(business_id, text)
    except Exception:
        log.exception("owner notify failed (reply capture)")


async def _forward_proof(business_id: str, name: str, media_b64: str, media_type: str,
                         caption: str) -> None:
    """Send the customer's payment screenshot to the owner as ONE message: the
    image WITH the full caption. This is the only owner nudge for a screenshot -
    we no longer send a second text alert (that caused the duplicate "they have
    paid, they have paid")."""
    try:
        db = require_db()
        biz = (db.table("businesses").select("whatsapp_number, plan")
               .eq("id", business_id).limit(1).execute()).data
        if not biz:
            return
        await whatsapp.send_message(
            business_id=business_id, to_number=biz[0]["whatsapp_number"],
            message_text=caption,
            plan=Plan(biz[0].get("plan") or "starter"),
            message_type=MessageType.owner_alert,
            image_base64=media_b64, image_media_type=media_type,
            language=Lang.hi, channel="platform")
    except Exception:
        log.exception("payment-proof forward failed")
        # Fallback: still ONE message (text only) if the image send failed.
        await _notify_owner(business_id, caption)


def _amt_phrase(amount) -> str:
    return f" ({inr(amount)})" if amount else ""


def _queue_payment(business_id: str, client: dict, amount) -> bool:
    """A KNOWN customer reported a specific amount paid: put it straight in the
    owner's Payments tab (party matched by their number) so the owner just checks
    the account and posts it into Tally. Deduped per party by receipts_queue.
    Returns True if a receipt was queued. Best-effort - never breaks the reply."""
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return False
    if amt <= 0:
        return False
    try:
        db = require_db()
        led = client.get("tally_ledger_name")
        if not led:
            r = (db.table("clients").select("tally_ledger_name, name")
                 .eq("id", client.get("id")).limit(1).execute()).data
            if r:
                led = r[0].get("tally_ledger_name") or r[0].get("name")
        disp = names.clean_display(client.get("name") or "") or (client.get("name") or "Customer")
        rq.create_pending(db, business_id, client_id=client.get("id"),
                          party_ledger=led or client.get("name") or disp,
                          party_display=disp, amount=amt)
        return True
    except Exception:
        log.exception("could not queue customer-reported payment")
        return False


async def capture_reply(client: dict, text: str, *, media_b64: str | None = None,
                        media_type: str = "image/jpeg") -> bool:
    """The pipeline. The customer is NEVER replied to. Returns True when ASVA
    acted (paused reminders and/or nudged the owner) so the bot stays silent to
    the customer; False to fall through to the bot's other handling."""
    business_id = client.get("business_id")
    client_id = client.get("id")
    name = client.get("name") or "Customer"
    text = (text or "").strip()

    # Remember every inbound reply (best-effort, before we act on it) so the
    # party's story on the tracker and the payment-behaviour dataset both accrue.
    _is_shot = bool(media_b64) and len(text) < 4
    try:
        conversations.record(require_db(), business_id, client_id,
                             "[payment screenshot]" if _is_shot else text,
                             intent="screenshot" if _is_shot else None)
    except Exception:
        pass

    # 1. A screenshot with no clear instruction: treat as payment proof. Try to
    #    read the amount off it (UPI/bank) so it can go straight to the Payments tab.
    if media_b64 and len(text) < 4:
        amount = None
        try:
            from app.services import ocr
            amount = await ocr.extract_payment_amount(media_b64, media_type)
        except Exception:
            log.exception("screenshot amount OCR failed")
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), amount=amount,
                        raw_text="[payment screenshot]", source="screenshot")
        # Build the ONE caption, then send the image with it - a single owner
        # message, never two.
        if _queue_payment(business_id, client, amount):
            caption = (
                f"{name} sent a payment screenshot for {inr(amount)}. It is in your "
                f"Payments tab - check the account and post it into Tally. Reminders "
                f"paused for {settings.promise_grace_days} days. Reply CHASE {name} to resume.")
        else:
            caption = (
                f"{name} sent a payment screenshot. ASVA PAUSED their reminders for "
                f"{settings.promise_grace_days} days (nothing was replied to {name}). "
                f"When the money lands, reply PAID {name} to record it (you confirm the "
                f"amount + account in the app, then it posts to Tally). "
                f"Reply CHASE {name} to resume now, or message {name} yourself.")
        await _forward_proof(business_id, name, media_b64, media_type, caption)
        return True

    if not text:
        return False

    # 2. Keyword fast-path: PAID / PAID <amount>. High trust, no AI.
    m = _PAID_RE.match(text)
    if m:
        amount = _extract_amount(m.group(1))
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), amount=amount, raw_text=text,
                        source="keyword")
        queued = _queue_payment(business_id, client, amount)
        if queued:
            await _notify_owner(business_id,
                f'{name} says they paid {inr(amount)}. It is in your Payments tab - '
                f"check the account and post it into Tally. Reminders paused for "
                f"{settings.promise_grace_days} days. Reply CHASE {name} to resume.")
            return True
        await _notify_owner(business_id,
            f'{name} replied: "{text[:200]}". ASVA read this as "already paid"'
            f'{_amt_phrase(amount)} and PAUSED their reminders for {settings.promise_grace_days} '
            f"days (nothing was replied to {name}). When it lands, reply PAID {name} to record "
            f"it (confirm the amount + account in the app, then it posts to Tally). "
            f"Reply CHASE {name} to resume reminders now.")
        return True

    # 3. Understand natural free text with Gemini (multilingual).
    verdict = await intent.classify(text)
    if verdict is None:
        # Gemini off or failed: do not guess and do not reply. Fall through.
        return False

    kind = verdict["intent"]
    conf = verdict["confidence"]
    amount = verdict.get("amount")

    # Not about payment -> let the bot's menu/greeting handling take over.
    if kind == "chatter":
        return False

    # 4. Confidence gate: dispute / unclear / low confidence -> owner decides, no pause.
    if kind in ("dispute", "unclear") or conf < settings.promise_confidence_threshold:
        await _notify_owner(business_id,
            f'{name} replied: "{text[:300]}". ASVA did NOT act on this (it needs your '
            f"eye). Reminders are unchanged. Message {name} yourself, or reply "
            f"CHASE {name} / STOP {name}.")
        return True

    # Confident paid_claim.
    if kind == "paid_claim":
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), amount=amount, raw_text=text,
                        source="text", confidence=conf)
        if _queue_payment(business_id, client, amount):
            await _notify_owner(business_id,
                f'{name} says they paid {inr(amount)} ("{text[:120]}"). It is in your '
                f"Payments tab - check the account and post it into Tally. Reminders "
                f"paused for {settings.promise_grace_days} days. Reply CHASE {name} to resume.")
            return True
        await _notify_owner(business_id,
            f'{name} replied: "{text[:200]}". ASVA read this as "already paid"'
            f'{_amt_phrase(amount)} and PAUSED their reminders for {settings.promise_grace_days} '
            f"days (nothing was replied to {name}). When it lands, reply PAID {name} to record "
            f"it (confirm the amount + account in the app, then it posts to Tally). "
            f"Reply CHASE {name} to resume reminders now.")
        return True

    # Confident promise with a date.
    if kind == "promise":
        pdate = verdict["promise_date"]
        promises.create(require_db(), business_id, client_id, kind="promise",
                        hold_until=_promise_hold(pdate), amount=amount,
                        promise_date=pdate, raw_text=text, source="text", confidence=conf)
        await _notify_owner(business_id,
            f'{name} replied: "{text[:200]}". ASVA read this as a promise to pay by '
            f"{pdate}{_amt_phrase(amount)} and PAUSED their reminders till then (nothing was "
            f"replied to {name}). Enter it in Tally when it lands. Reply CHASE {name} to "
            f"resume reminders now.")
        return True

    return False
