"""Reply capture pipeline: turn a customer's WhatsApp reply into an action.

One entry point, capture_reply(), called from the customer branch of the bot.
It reads the reply, decides what it means, and (for a payment claim or a
promised date) HOLDS that party's reminders with a grace window that
auto-resumes, notifies the owner to record real payments in Tally, and thanks
the customer. It never marks a bill paid and never writes Tally.

Trust posture (agreed design): auto-hold with grace, auto-resume. A misread or a
low-confidence reply is NOT auto-held - it is forwarded to the owner instead, so
a false "paid" never buys permanent silence and a wrong classification never
silences a real debt for more than the capped window.

Order of understanding:
  1. keyword fast-path  (PAID / PAID <amount>) - zero cost, high trust
  2. a screenshot       - forwarded to the owner as proof, held on grace
  3. Gemini classify    - for natural, multilingual free text
  4. confidence gate    - dispute / unclear / low confidence -> forward to owner
Returns the customer-facing reply string, or None to let the bot fall through
(chatter, or nothing we can act on -> the bot's existing menu/silent handling).
"""
from __future__ import annotations

import datetime as _dt
import logging
import re

from app.config import settings
from app.db import require_db
from app.models import Lang, MessageType, Plan
from app.services import intent, promises, whatsapp
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


def _en(business_id: str) -> bool:
    """Best-effort: does this business message in English? Defaults to True (the
    pilot shops do); never fails the reply over a lookup."""
    try:
        r = (require_db().table("businesses").select("msg_language")
             .eq("id", business_id).limit(1).execute()).data
        return not r or str(r[0].get("msg_language") or "english").lower() == "english"
    except Exception:
        return True


async def _notify_owner(business_id: str, text: str) -> None:
    try:
        await whatsapp.notify_owner(business_id, text)
    except Exception:
        log.exception("owner notify failed (reply capture)")


async def _forward_proof(business_id: str, name: str, media_b64: str, media_type: str) -> None:
    """Send the customer's payment screenshot to the owner as proof."""
    try:
        db = require_db()
        biz = (db.table("businesses").select("whatsapp_number, plan")
               .eq("id", business_id).limit(1).execute()).data
        if not biz:
            return
        await whatsapp.send_message(
            business_id=business_id, to_number=biz[0]["whatsapp_number"],
            message_text=(f"{name} sent a payment screenshot. If the money has come, "
                          f"enter it in Tally. To chase anyway, reply CHASE {name}."),
            plan=Plan(biz[0].get("plan") or "starter"),
            message_type=MessageType.owner_alert,
            image_base64=media_b64, image_media_type=media_type,
            language=Lang.hi, channel="platform")
    except Exception:
        log.exception("payment-proof forward failed")
        await _notify_owner(business_id, f"{name} sent a payment screenshot on WhatsApp.")


def _amt_phrase(amount) -> str:
    return f" ({inr(amount)})" if amount else ""


async def capture_reply(client: dict, text: str, *, media_b64: str | None = None,
                        media_type: str = "image/jpeg") -> str | None:
    """The pipeline. Returns the reply to send the customer, or None to fall
    through to the bot's existing handling."""
    business_id = client.get("business_id")
    client_id = client.get("id")
    name = client.get("name") or "Customer"
    text = (text or "").strip()
    en = _en(business_id)

    # 1. A screenshot with no clear instruction: treat as payment proof.
    if media_b64 and len(text) < 4:
        await _forward_proof(business_id, name, media_b64, media_type)
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), raw_text="[payment screenshot]",
                        source="screenshot")
        return ("Thank you, we have received your payment proof and informed the shop."
                if en else "Shukriya, aapka payment proof mil gaya aur dukaan ko bata diya.")

    if not text:
        return None

    # 2. Keyword fast-path: PAID / PAID <amount>. High trust, no AI.
    m = _PAID_RE.match(text)
    if m:
        amount = _extract_amount(m.group(1))
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), amount=amount, raw_text=text,
                        source="keyword")
        await _notify_owner(business_id,
            f"{name} says they have paid{_amt_phrase(amount)}. Reminders are paused "
            f"for {settings.promise_grace_days} days. Please enter the receipt in Tally "
            f"when it lands, ASVA updates on its own. To chase anyway, reply CHASE {name}.")
        return ("Thank you, we have noted your payment. The shop has been informed."
                if en else "Shukriya, aapka payment note kar liya. Dukaan ko bata diya hai.")

    # 3. Understand natural free text with Gemini (multilingual).
    verdict = await intent.classify(text)
    if verdict is None:
        # Gemini off or failed: do not guess. Let the bot fall through (silent).
        return None

    kind = verdict["intent"]
    conf = verdict["confidence"]
    amount = verdict.get("amount")

    # Not about payment -> let the bot's menu/greeting handling take over.
    if kind == "chatter":
        return None

    # 4. Confidence gate: dispute / unclear / low confidence -> owner decides.
    if kind in ("dispute", "unclear") or conf < settings.promise_confidence_threshold:
        await _notify_owner(business_id,
            f'{name} replied: "{text[:300]}". This needs your eye, ASVA did not act on it.')
        return ("Thank you, we have passed this to the shop. They will get back to you."
                if en else "Shukriya, aapki baat dukaan tak pahuncha di. Jaldi jawab milega.")

    # Confident paid_claim.
    if kind == "paid_claim":
        promises.create(require_db(), business_id, client_id, kind="paid_claim",
                        hold_until=_grace_hold(), amount=amount, raw_text=text,
                        source="text", confidence=conf)
        await _notify_owner(business_id,
            f"{name} says they have paid{_amt_phrase(amount)}. Reminders are paused "
            f"for {settings.promise_grace_days} days. Enter the receipt in Tally when it "
            f"lands. To chase anyway, reply CHASE {name}.")
        return ("Thank you, we have noted your payment. The shop has been informed."
                if en else "Shukriya, aapka payment note kar liya. Dukaan ko bata diya hai.")

    # Confident promise with a date.
    if kind == "promise":
        pdate = verdict["promise_date"]
        promises.create(require_db(), business_id, client_id, kind="promise",
                        hold_until=_promise_hold(pdate), amount=amount,
                        promise_date=pdate, raw_text=text, source="text", confidence=conf)
        await _notify_owner(business_id,
            f"{name} says they will pay by {pdate}{_amt_phrase(amount)}. Reminders are "
            f"paused till then. Enter it in Tally when it lands. To chase now, reply CHASE {name}.")
        return ("Thank you. We have noted that and will follow up then."
                if en else f"Shukriya, humne note kar liya. {pdate} ke aas paas dobara puchhenge.")

    return None
