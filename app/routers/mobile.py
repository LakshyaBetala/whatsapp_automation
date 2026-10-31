"""ASVA on the owner's phone: the control room, not a reference screen.

The phone used to be read-only and the owner drove ASVA by typing commands at
the bot on WhatsApp (LIST, STOP, REMIND, PAID, TERMS). That was backwards. A
list of 1,000 parties does not belong in a chat thread, and a shopkeeper should
not have to remember a verb to pause one customer. So every owner action moved
here, and the bot went back to what a chat is good at: alerts, the morning and
evening summary, and answering a question.

Tally reading and WhatsApp sending still happen where they always did (the shop
PC and the server). The phone triggers them; it does not become a second engine.

Safety, by construction:
  - every write calls the SAME endpoint the desktop Command Center calls, so
    the phone can never do something the desktop cannot and tenant scoping
    (business_id) stays implemented in exactly one place;
  - auth is the shop's own agent token, in the POST body, never a cookie, so
    there is nothing for a third-party page to forge;
  - the service-role key never leaves the server.

Endpoints:
  GET  /m                      -> the installable app shell
  GET  /m/manifest.webmanifest -> PWA manifest
  GET  /m/sw.js                -> offline shell service worker
  GET  /m/api/summary          -> totals, who to chase, where the shop stands
  GET  /m/api/party?id=        -> one party: dues, replies, promise, settings
  GET  /m/api/today            -> the morning brief, same source as the 8am message
  GET  /m/api/money            -> recovered, coming in, open promises
  GET  /m/api/picks            -> who ASVA suggests chasing first
  POST /m/api/remind           -> send this party's reminder now
  POST /m/api/record-payment   -> book a payment into Tally
  POST /m/api/reminders        -> reminders on/off for one party
  POST /m/api/exclude          -> move a party in/out of do-not-chase
  POST /m/api/terms            -> change one party's credit days
  POST /m/api/number           -> add or fix a party's WhatsApp number
  POST /m/api/release          -> drop a promise hold and chase again
  POST /m/api/pick             -> switch reminders on for the chosen parties
  POST /m/api/bill             -> add a bill that is not in Tally
  POST /m/api/letter           -> read the formal reminder before sending it
  POST /m/api/letter/send      -> send that formal reminder (owner approved)
  GET  /m/api/inbox            -> payment claims waiting on the owner
  POST /m/api/pending/confirm  -> book a claimed payment into Tally
  POST /m/api/pending/cancel   -> it was not a payment
  POST /m/api/signal/dismiss   -> clear a pay-page tap
  GET  /m/api/checkpoint       -> today's list, before it goes out
  POST /m/api/hold             -> hold one party today (or all of them)
  POST /m/api/photo-bill       -> read a bill from a photo
  POST /m/api/photo-bill/save  -> turn that reading into a real bill
  POST /m/api/party            -> add a party that is not in Tally yet
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.db import require_db
from app.services import conversations, names, promises, proof

router = APIRouter(prefix="/m", tags=["mobile"])

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _biz(token: str) -> dict:
    """Resolve the shop from its token. Read-only; 401 on a bad token."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = require_db()
    r = (db.table("businesses").select("id, business_name")
         .eq("agent_token", token).limit(1).execute())
    if not r.data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return r.data[0]


def _inr(v) -> str:
    """Indian-grouped rupees (8,43,000) - NOT Python's western ',' (843,000).
    A shopkeeper reads lakhs/crores; western grouping misreads at a glance."""
    from app.services.templates import inr_digits
    try:
        return inr_digits(Decimal(str(v)))
    except Exception:
        return "0"


def _overdue_days(due, today) -> int:
    try:
        return max(0, (today - _dt.date.fromisoformat(str(due))).days)
    except (TypeError, ValueError):
        return 0


def _build_summary(db, biz: dict) -> dict:
    today = _dt.datetime.now(IST).date()
    bid = biz["id"]
    clients = (db.table("clients")
               .select("id, name, whatsapp_number, excluded, reminders_enabled")
               .eq("business_id", bid).execute()).data or []
    by_id = {c["id"]: c for c in clients}
    open_bills = (db.table("bills")
                  .select("client_id, outstanding, due_date, status")
                  .eq("business_id", bid)
                  .in_("status", ["pending", "partial", "overdue"]).execute()).data or []

    owed: dict = {}
    worst_due: dict = {}
    for b in open_bills:
        cid = b.get("client_id")
        if cid not in by_id:
            continue
        owed[cid] = owed.get(cid, Decimal(0)) + Decimal(str(b.get("outstanding") or 0))
        od = _overdue_days(b.get("due_date"), today)
        worst_due[cid] = max(worst_due.get(cid, 0), od)

    held = promises.held_now(db, [bid]).get(bid, set())
    # "Reminders on" is the number that matters most on this screen now: a shop
    # can have a crore outstanding and ASVA chasing nobody, and before the pick
    # flow existed nothing on any screen said so.
    flags = (db.table("clients").select("id, reminders_enabled, excluded")
             .eq("business_id", bid).is_("archived_at", "null").execute()).data or []
    on_n = sum(1 for c in flags if c.get("reminders_enabled") and not c.get("excluded"))

    parties = []
    total_out = Decimal(0)
    owing_n = 0
    for cid, amt in owed.items():
        if amt <= 0:
            continue
        c = by_id[cid]
        if c.get("excluded"):
            continue
        owing_n += 1
        total_out += amt
        parties.append({
            "id": cid,
            "name": names.clean_display(c.get("name") or "") or "(unnamed)",
            "outstanding": _inr(amt),
            "amt": float(amt),                 # numeric -> phone sorts by amount
            "overdue_days": worst_due.get(cid, 0),
            "paused": cid in held,
            # the Parties tab filters on this, so a party ASVA is ignoring is
            # findable instead of silently absent
            "reminders_on": bool(c.get("reminders_enabled")),
            "whatsapp": c.get("whatsapp_number") or "",   # phone can offer Call
        })
    # Full owing list, ordered as the default "who to chase": active parties
    # first (most overdue, then biggest), paused/on-promise parties last. The
    # phone searches, sorts and pages this list locally, so every owing party
    # (not just the top 100) is reachable from the phone.
    chase = sorted(parties, key=lambda p: (p["paused"], -p["overdue_days"], -p["amt"]))
    pf = proof.build_proof(db, bid, today)
    from app.services import shortlist
    stage = shortlist.stage(
        [{"id": c["id"], "reminders_enabled": c.get("reminders_enabled"),
          "excluded": c.get("excluded"), "reminder_anchor": c.get("reminder_anchor")}
         for c in (db.table("clients")
                   .select("id, reminders_enabled, excluded, reminder_anchor")
                   .eq("business_id", bid).is_("archived_at", "null")
                   .execute()).data or []], today)
    return {
        "business_name": biz.get("business_name") or "Your shop",
        "total_outstanding": _inr(total_out),
        "parties_owing": owing_n,
        "reminders_on": on_n,
        "stage": stage["what"],                    # pick | review | none
        "stage_on": stage["on"],
        "stage_since": stage["since"].isoformat() if stage["since"] else None,
        "on_promise": sum(1 for p in parties if p["paused"]),
        "recovered_this_month": _inr(pf["recovered_this_month"]),
        "recovered_month": pf["month"],
        "tally_synced": _tally_ago(db, bid),
        "chase": chase,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


def _tally_ago(db, bid: str) -> str:
    """"today" / "3 days ago" / "never". A phone that shows a crore outstanding
    without saying the figure is three weeks old is lying politely."""
    try:
        r = (db.table("businesses").select("last_tally_sync_at")
             .eq("id", bid).limit(1).execute()).data or []
        raw = (r[0] or {}).get("last_tally_sync_at") if r else None
        if not raw:
            return "never"
        t = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        d = (_dt.datetime.now(_dt.timezone.utc) - t).days
        return "today" if d <= 0 else ("1 day ago" if d == 1 else f"{d} days ago")
    except Exception:
        return ""


def _build_party(db, biz: dict, client_id: str) -> dict:
    today = _dt.datetime.now(IST).date()
    bid = biz["id"]
    cr = (db.table("clients")
          .select("id, name, whatsapp_number, credit_days, excluded, reminders_enabled")
          .eq("id", client_id).eq("business_id", bid).limit(1).execute())
    if not cr.data:
        raise HTTPException(status_code=404, detail="Party not found")
    c = cr.data[0]
    bills = (db.table("bills")
             .select("invoice_number, outstanding, amount, due_date, invoice_date, status")
             .eq("business_id", bid).eq("client_id", client_id)
             .order("invoice_date", desc=True).limit(200).execute()).data or []
    open_bills = [b for b in bills if b.get("status") in ("pending", "partial", "overdue")]
    settled = [b for b in bills if b.get("status") not in
               ("pending", "partial", "overdue")]
    total = sum(Decimal(str(b.get("outstanding") or 0)) for b in open_bills)

    promise = None
    pr = promises.find_open(db, bid, client_id)
    if pr:
        promise = {
            "kind": pr.get("kind"),
            "promise_date": str(pr.get("promise_date")) if pr.get("promise_date") else None,
            "hold_until": str(pr.get("hold_until") or "")[:10],
            "said": pr.get("raw_text") or "",
            "when": str(pr.get("created_at") or "")[:10],
        }
    recent = [{
        "text": r.get("body") or "",
        "when": str(r.get("created_at") or "")[:10],
    } for r in conversations.recent_for_client(db, bid, client_id, limit=8)]
    return {
        "id": c["id"],
        "name": names.clean_display(c.get("name") or "") or "(unnamed)",
        "whatsapp": c.get("whatsapp_number") or "",
        "excluded": bool(c.get("excluded")),
        "reminders_on": bool(c.get("reminders_enabled", True)),
        "credit_days": int(c.get("credit_days") or 30),
        "outstanding": _inr(total),
        "open_bills": [{
            "invoice": b.get("invoice_number") or "-",
            "amount": _inr(b.get("outstanding") or 0),
            "overdue_days": _overdue_days(b.get("due_date"), today),
            "due": str(b.get("due_date") or "")[:10],
        } for b in open_bills[:60]],
        "recent": recent,
        "promise": promise,
        "schedule": _schedule(db, biz, c, open_bills, today),
        # "Did bill 2291 ever get paid?" had no answer on the phone.
        "settled_bills": [{
            "invoice": b.get("invoice_number") or "-",
            "amount": _inr(b.get("amount") or 0),
            "date": str(b.get("invoice_date") or "")[:10],
        } for b in settled[:40]],
        "settled_count": len(settled),
    }


def _schedule(db, biz: dict, client: dict, open_bills: list, today) -> dict:
    """When this party next hears from ASVA, in words.

    The owner's most common question after "how much" is "when does he get
    the next message", and the only place that existed was the desktop.
    """
    try:
        from app.routers.admin import _next_reminder, _client_anchor
        row = (db.table("businesses").select(
            "id, reminder_style, reminder_cadence, overdue_repeat_days, "
            "overdue_max_repeats, weekly_off_day, blackout_dates, reminder_hour")
            .eq("id", biz["id"]).limit(1).execute()).data or [{}]
        full = {**biz, **(row[0] or {})}
        label, colour = _next_reminder(
            full, open_bills, int(client.get("credit_days") or 30), today,
            _client_anchor(client))
        return {"label": label or "", "colour": colour or ""}
    except Exception:
        return {"label": "", "colour": ""}


# ── Read-only API ───────────────────────────────────────────────────────────
@router.get("/api/summary")
def api_summary(token: str = Query("")):
    return JSONResponse(_build_summary(require_db(), _biz(token)))


@router.get("/api/party")
def api_party(token: str = Query(""), id: str = Query("")):
    return JSONResponse(_build_party(require_db(), _biz(token), id))


# ── Owner actions from the phone (reuse the desktop endpoints, same token/auth,
# so behaviour never diverges). The shop's WhatsApp sends still leave the shop
# laptop via the outbox - the phone only triggers them. ─────────────────────
class _MRemind(BaseModel):
    token: str
    party: str


class _MPay(BaseModel):
    token: str
    client_id: str
    amount: float
    payment_date: str | None = None


class _MFlag(BaseModel):
    token: str
    client_id: str
    on: bool


class _MDays(BaseModel):
    token: str
    client_id: str
    days: int


class _MNumber(BaseModel):
    token: str
    client_id: str
    number: str


class _MParty(BaseModel):
    token: str
    client_id: str


class _MPick(BaseModel):
    token: str
    client_ids: list[str] = []


class _MBill(BaseModel):
    token: str
    client_id: str
    amount: float
    invoice_number: str = ""
    invoice_date: str | None = None


@router.post("/api/remind")
async def api_remind(p: _MRemind):
    _biz(p.token)                                  # 401 on a bad token
    from app.routers.admin import admin_send_now, SendNowPayload
    return await admin_send_now(SendNowPayload(token=p.token, party=p.party))


@router.post("/api/record-payment")
async def api_record_payment(p: _MPay):
    _biz(p.token)                                  # 401 on a bad token
    from app.routers.admin import admin_record_payment, RecordPaymentPayload
    return await admin_record_payment(RecordPaymentPayload(
        token=p.token, client_id=p.client_id, amount=p.amount,
        payment_date=p.payment_date))


@router.post("/api/reminders")
def api_reminders(p: _MFlag):
    """Pause or resume one party. Was "STOP Ramesh" typed at a chat bot."""
    _biz(p.token)
    from app.routers.admin import admin_set_reminder, SetReminderPayload
    return admin_set_reminder(SetReminderPayload(
        token=p.token, client_id=p.client_id, enabled=p.on))


@router.post("/api/exclude")
def api_exclude(p: _MFlag):
    """Do-not-chase. `on` true means "never chase this party"."""
    _biz(p.token)
    from app.routers.admin import admin_set_excluded, SetExcludedPayload
    return admin_set_excluded(SetExcludedPayload(
        token=p.token, client_id=p.client_id, excluded=p.on))


@router.post("/api/terms")
def api_terms(p: _MDays):
    _biz(p.token)
    from app.routers.admin import admin_set_credit_days, CreditDaysPayload
    return admin_set_credit_days(CreditDaysPayload(
        token=p.token, client_id=p.client_id, days=p.days))


@router.post("/api/number")
def api_number(p: _MNumber):
    """A party with no number is a party ASVA cannot chase, and the owner has
    the number in his phone contacts, not on the shop PC. This is the single
    most useful thing the phone can do that the desktop cannot."""
    _biz(p.token)
    from app.routers.admin import admin_set_number, SetNumberPayload
    return admin_set_number(SetNumberPayload(
        token=p.token, client_id=p.client_id, number=p.number))


@router.post("/api/release")
async def api_release(p: _MParty):
    """"He said he would pay Friday" put this party on hold. Friday came and
    went. Drop the hold and let the cadence chase again."""
    _biz(p.token)
    from app.routers.admin import admin_promise_action, PromiseActionPayload
    return await admin_promise_action(PromiseActionPayload(
        token=p.token, client_id=p.client_id, action="chase"))


@router.post("/api/pick")
def api_pick(p: _MPick):
    """Start with ten. The onboarding that decides whether a shop keeps ASVA
    happens on the phone, because that is where the owner actually is."""
    biz = _biz(p.token)
    from app.services import shortlist
    n = shortlist.enable(require_db(), biz["id"], p.client_ids)
    return JSONResponse({"ok": True, "enabled": n})


@router.post("/api/bill")
def api_bill(p: _MBill):
    """A bill that never went through Tally: a cash sale, a slip, an old
    balance. Was "BILL Ramesh 5000" at the bot."""
    _biz(p.token)
    from app.routers.admin import admin_nt_bill_add, NTBillAddPayload
    return admin_nt_bill_add(NTBillAddPayload(
        token=p.token, client_id=p.client_id, amount=p.amount,
        invoice_number=p.invoice_number, invoice_date=p.invoice_date))


@router.post("/api/letter")
async def api_letter(p: _MParty):
    """The formal reminder, for reading only. Escalation is never automatic and
    never one tap: the owner sees the exact words first. Was LETTER at the bot,
    where the preview and the send looked identical."""
    _biz(p.token)
    from app.routers.admin import admin_letter_preview, LetterPayload
    return await admin_letter_preview(LetterPayload(
        token=p.token, client_id=p.client_id))


@router.post("/api/letter/send")
async def api_letter_send(p: _MParty):
    _biz(p.token)
    from app.routers.admin import admin_send_letter, LetterPayload
    return await admin_send_letter(LetterPayload(
        token=p.token, client_id=p.client_id))


class _MId(BaseModel):
    token: str
    id: str


class _MConfirm(BaseModel):
    token: str
    id: str
    amount: float | None = None
    deposit_ledger: str | None = None
    receipt_date: str | None = None


class _MHold(BaseModel):
    token: str
    client_id: str = ""
    all: bool = False


class _MPhoto(BaseModel):
    token: str
    image_b64: str
    image_type: str = "image/jpeg"


class _MPhotoSave(BaseModel):
    """What the owner corrected on screen before saving the read."""
    token: str
    party_name: str = ""
    bill_number: str = ""
    amount: float | None = None
    bill_date: str | None = None


class _MNewParty(BaseModel):
    token: str
    name: str
    whatsapp_number: str = ""
    credit_days: int | None = None


@router.post("/api/party")
def api_new_party(p: _MNewParty):
    """A party who is not in Tally yet: a cash customer, a new buyer, someone
    the owner just agreed terms with while standing in his shop. That moment
    never happens at the desktop."""
    _biz(p.token)
    from app.routers.admin import admin_party_create, PartyCreatePayload
    return admin_party_create(PartyCreatePayload(
        token=p.token, name=p.name, whatsapp_number=p.whatsapp_number,
        credit_days=p.credit_days))


@router.get("/api/inbox")
def api_inbox(token: str = Query("")):
    """Customers who say they have paid, waiting on the owner to agree.

    In the chat this arrived as a nudge and was settled by replying PAID or
    YES, which meant scrolling back up a thread to find it. It is a list now.
    """
    _biz(token)
    from app.routers.admin import admin_payments_data
    d = admin_payments_data(token=token)
    return JSONResponse({
        "pending": d.get("pending") or [],
        "signals": d.get("pay_signals") or [],
        "deposit_ledgers": d.get("deposit_ledgers") or [],
    })


@router.post("/api/pending/confirm")
def api_pending_confirm(p: _MConfirm):
    _biz(p.token)
    from app.routers.admin import admin_pending_confirm, PendingConfirmPayload
    return admin_pending_confirm(PendingConfirmPayload(
        token=p.token, id=p.id, amount=p.amount,
        deposit_ledger=p.deposit_ledger, receipt_date=p.receipt_date))


@router.post("/api/pending/cancel")
def api_pending_cancel(p: _MId):
    _biz(p.token)
    from app.routers.admin import admin_pending_cancel, PendingCancelPayload
    return admin_pending_cancel(PendingCancelPayload(token=p.token, id=p.id))


@router.post("/api/signal/dismiss")
def api_signal_dismiss(p: _MId):
    _biz(p.token)
    from app.routers.admin import admin_pay_signal_dismiss, PaySignalPayload
    return admin_pay_signal_dismiss(PaySignalPayload(token=p.token, id=p.id))


@router.get("/api/checkpoint")
def api_checkpoint(token: str = Query("")):
    """Today's list before it goes out, with who is already held.

    This is the morning checkpoint the chat used to ask about. Holding a
    party who has already paid is a thirty-second job that stops an
    embarrassing message, and it belongs on a screen, not in a thread.
    """
    biz = _biz(token)
    from app.services import checkpoint
    cp = checkpoint.get_today(require_db(), biz["id"]) or {}
    held = set(cp.get("held") or [])
    items = []
    for it in (cp.get("items") or []):
        cid = it.get("client_id") or it.get("id")
        items.append({
            "client_id": cid,
            "name": names.clean_display(it.get("name") or "") or "(unnamed)",
            "amount": _inr(it.get("amount") or 0),
            "held": cid in held,
        })
    return JSONResponse({"open": bool(cp), "items": items, "held": len(held)})


@router.post("/api/hold")
def api_hold(p: _MHold):
    """Skip today's reminder for a party who has already paid. Nothing is
    marked paid and nothing is written to Tally: the party simply comes back
    on its next cadence day, which is what the chat's HOLD always did."""
    biz = _biz(p.token)
    from app.services import checkpoint
    db = require_db()
    if p.all:
        return JSONResponse({"ok": True, "held": checkpoint.hold_all(db, biz["id"])})
    if not p.client_id:
        raise HTTPException(status_code=400, detail="Pick a party to hold.")
    checkpoint.hold(db, biz["id"], p.client_id)
    return JSONResponse({"ok": True, "held": 1})


@router.post("/api/photo-bill")
async def api_photo_bill(p: _MPhoto):
    """Read a bill from a photo. The camera is on the phone, so this is the
    one thing the chat did that the phone had the better claim to."""
    biz = _biz(p.token)
    from app.services import bot, ocr
    if not ocr.is_configured():
        return JSONResponse({"ok": False,
                             "detail": "Photo bills are not set up on this shop."})
    await bot._handle_photo_bill({"id": biz["id"],
                                  "business_name": biz.get("business_name") or ""},
                                 p.image_b64, p.image_type)
    pb = await bot._latest_pending_photo_bill(biz["id"])
    if not pb:
        return JSONResponse({"ok": False,
                             "detail": "Could not read that photo. Try again in "
                                       "good light, straight from above."})
    return JSONResponse({"ok": True, "read": {
        "party_name": pb.get("party_name") or "",
        "phone": pb.get("phone") or "",
        "amount": pb.get("amount"),
        "bill_number": pb.get("bill_number") or "",
        "bill_date": str(pb.get("bill_date") or "")[:10],
    }})


@router.post("/api/photo-bill/save")
async def api_photo_bill_save(p: _MPhotoSave):
    """Confirm the reading (after any edits) and create the bill."""
    biz = _biz(p.token)
    from app.services import bot
    db = require_db()
    patch = {}
    for k in ("party_name", "bill_number"):
        v = (getattr(p, k, "") or "").strip()
        if v:
            patch[k] = v
    if p.amount is not None:
        patch["amount"] = p.amount
    if p.bill_date:
        patch["bill_date"] = p.bill_date
    if patch:
        db.table("photo_bills").update(patch).eq(
            "business_id", biz["id"]).eq("status", "pending").execute()
    msg = await bot._confirm_photo_bill({"id": biz["id"],
                                         "business_name": biz.get("business_name") or "",
                                         "plan": biz.get("plan") or "starter"})
    return JSONResponse({"ok": True, "message": msg})


@router.get("/api/today")
def api_today(token: str = Query("")):
    """The morning brief, built by the SAME function that writes the good
    morning WhatsApp message (services/today.build_today).

    This matters more than it looks. The owner reads a message at 8am naming
    six parties, opens the app, and has to see those six parties. Two lists
    built two ways drift apart within a week and the app stops being trusted.
    """
    biz = _biz(token)
    db = require_db()
    row = (db.table("businesses")
           .select("id, business_name, setup_problem, last_tally_sync_at, "
                   "wa_ready, wa_checked_at, wa_transport")
           .eq("id", biz["id"]).limit(1).execute()).data or [{}]
    full = {**biz, **(row[0] or {})}
    from app.services import today as today_svc
    snap = today_svc.build_today(db, biz["id"], full)
    # What the brief cannot know: whether this shop has pointed ASVA at anyone
    # yet, and whether each named party is actually being chased.
    flags = (db.table("clients")
             .select("id, reminders_enabled, excluded, reminder_anchor")
             .eq("business_id", biz["id"]).is_("archived_at", "null").execute()).data or []
    on = {c["id"] for c in flags if c.get("reminders_enabled") and not c.get("excluded")}
    from app.services import shortlist
    st = shortlist.stage(flags)
    for row_ in snap.get("chase") or []:
        row_["reminders_on"] = row_.get("client_id") in on
    snap["reminders_on_count"] = len(on)
    snap["stage"] = st["what"]
    snap["stage_on"] = st["on"]
    snap["tally_synced"] = _tally_ago(db, biz["id"])
    snap["sent_today"] = _sent_today(db, biz["id"])
    snap["wa"] = _wa_health(full)
    return JSONResponse(snap)


def _wa_health(biz: dict) -> dict:
    """Is the shop's WhatsApp actually able to send right now?

    The phone could show a crore outstanding and a tidy chase list while
    nothing had left the building for a week, because nothing on any phone
    screen said the number was disconnected. Stale checks are reported as
    unknown rather than guessed at.
    """
    ready = biz.get("wa_ready")
    checked = biz.get("wa_checked_at")
    fresh = False
    if checked:
        try:
            t = _dt.datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
            fresh = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() < 45 * 60
        except (TypeError, ValueError):
            fresh = False
    state = "unknown"
    if fresh and ready is True:
        state = "up"
    elif fresh and ready is False:
        state = "down"
    return {"state": state, "transport": biz.get("wa_transport") or "laptop"}


def _sent_today(db, bid: str) -> int:
    """How many reminders actually left today. Cadence markers are bookkeeping
    rows, not messages, so they are excluded - counting them was how a shop that
    sent nothing could still look busy."""
    try:
        start = _dt.datetime.now(IST).replace(hour=0, minute=0, second=0,
                                              microsecond=0).astimezone(_dt.timezone.utc)
        rows = (db.table("messages").select("id, template_name")
                .eq("business_id", bid).eq("delivery_status", "sent")
                .gte("created_at", start.isoformat()).limit(2000).execute()).data or []
        return sum(1 for r in rows if r.get("template_name") != "cadence_marker")
    except Exception:
        return 0


@router.get("/api/money")
def api_money(token: str = Query("")):
    """Recovered, coming in, and every promise still open. The three money
    questions an owner asks away from his desk."""
    biz = _biz(token)
    db = require_db()
    bid = biz["id"]
    today = _dt.datetime.now(IST).date()
    pf = proof.build_proof(db, bid, today)
    from app.services import forecast
    f = forecast.cash_in_forecast(db, bid, horizon_days=7, today=today)
    rows = promises.open_for_business(db, bid)
    cids = [r.get("client_id") for r in rows if r.get("client_id")]
    nm = {}
    if cids:
        for i in range(0, len(cids), 100):
            for c in (db.table("clients").select("id, name")
                      .eq("business_id", bid).in_("id", cids[i:i + 100])
                      .execute()).data or []:
                nm[c["id"]] = names.clean_display(c.get("name") or "")
    return JSONResponse({
        "recovered_this_month": _inr(pf["recovered_this_month"]),
        "recovered_month": pf["month"],
        "recovered_last_month": _inr(pf.get("recovered_last_month") or 0),
        "billed_this_month": _inr(pf.get("billed_this_month") or 0),
        "collected_pct": pf.get("collected_pct"),
        "coming_total": _inr(f["total"]),
        "coming_promised": _inr(f["promised"]),
        "coming_due": _inr(f["due_soon"]),
        "coming_days": f["horizon_days"],
        "promises": [{
            "client_id": r.get("client_id"),
            "name": nm.get(r.get("client_id")) or "(unnamed)",
            "kind": r.get("kind") or "",
            "promise_date": str(r.get("promise_date") or "")[:10],
            "hold_until": str(r.get("hold_until") or "")[:10],
            "said": r.get("raw_text") or "",
        } for r in rows],
    })


@router.get("/api/picks")
def api_picks(token: str = Query(""), n: int = Query(10)):
    """Who to chase first: worst money, longest stuck, that ASVA is ignoring."""
    biz = _biz(token)
    db = require_db()
    from app.services import shortlist
    today = _dt.datetime.now(IST).date()
    clients = (db.table("clients")
               .select("id, name, whatsapp_number, reminders_enabled, excluded")
               .eq("business_id", biz["id"]).is_("archived_at", "null")
               .execute()).data or []
    bills = (db.table("bills").select("client_id, outstanding, due_date")
             .eq("business_id", biz["id"])
             .in_("status", ["pending", "partial", "overdue"]).execute()).data or []
    totals: dict = {}
    overdue: dict = {}
    for b in bills:
        cid = b.get("client_id")
        if not cid:
            continue
        totals[cid] = totals.get(cid, Decimal(0)) + Decimal(str(b.get("outstanding") or 0))
        od = _overdue_days(b.get("due_date"), today)
        if od > 0:
            overdue[cid] = max(overdue.get(cid, 0), od)
    want = max(1, min(int(n or 10), 50))
    return JSONResponse({"picks": [{
        "id": r["id"],
        "name": names.clean_display(r["name"]) or "(unnamed)",
        "outstanding": _inr(r["amount"]),
        "days": r["days"],
    } for r in shortlist.rank(clients, totals, overdue)[:want]]})


@router.get("/qr")
def phone_link_qr(token: str = Query("")):
    """A QR the desktop app shows so the owner scans it to open their OWN phone
    app - it encodes /m?token=<their token>, so the phone lands already linked to
    their shop's live data. Token is verified first, so this is not an open QR
    maker."""
    _biz(token)                       # 401s on a bad token
    import base64
    from fastapi.responses import Response
    from app.config import settings
    from app.services import upi
    base = (settings.public_base_url or "https://app.tryasva.com").rstrip("/")
    b64 = upi.qr_png_base64(f"{base}/m?token={token}")
    if not b64:
        raise HTTPException(status_code=503, detail="QR not available")
    return Response(content=base64.b64decode(b64), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


# ── PWA shell + manifest + service worker ───────────────────────────────────
@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "ASVA", "short_name": "ASVA", "start_url": "/m",
        "display": "standalone", "background_color": "#f4f5f2",
        "theme_color": "#ffffff",
        # The real ASVA mark, already served by the site router on this host.
        # It was a hand-drawn green chevron, which is what owners would have
        # had sitting on their home screen.
        "icons": [
            {"src": "/logo.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/logo.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


@router.get("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           '<stop offset="0" stop-color="#12a150"/><stop offset="1" stop-color="#0a7d33"/>'
           '</linearGradient></defs>'
           '<rect width="100" height="100" rx="24" fill="#0f1512"/>'
           '<path d="M50 24 L72 74 H61 L50 47 L39 74 H28 Z" fill="url(#g)"/></svg>')
    return PlainTextResponse(svg, media_type="image/svg+xml")


@router.get("/sw.js")
def service_worker():
    # Offline shell, network FIRST.
    #
    # The first version of this cached /m and served it cache-first forever
    # under a cache name that never changed, so an owner who had opened the app
    # once would keep the old screen through every deploy. A collections app
    # that cannot be updated is worse than one that needs the network: the shell
    # is now fetched fresh when there is a connection and only falls back to the
    # copy on disk when there is not.
    js = (
        "const C='asva-m-v2';"
        "self.addEventListener('install',e=>{self.skipWaiting();"
        "e.waitUntil(caches.open(C).then(c=>c.addAll(['/m'])))});"
        "self.addEventListener('activate',e=>{e.waitUntil("
        "caches.keys().then(k=>Promise.all(k.filter(n=>n!==C).map(n=>caches.delete(n))))"
        ".then(()=>self.clients.claim()))});"
        "self.addEventListener('fetch',e=>{const u=new URL(e.request.url);"
        "if(u.pathname.startsWith('/m/api')){return;}"          # never cache data
        "if(e.request.method!=='GET'){return;}"
        "e.respondWith(fetch(e.request).then(r=>{"
        "const c=r.clone();caches.open(C).then(x=>x.put(e.request,c));return r;"
        "}).catch(()=>caches.match(e.request)))});"
    )
    return PlainTextResponse(js, media_type="application/javascript",
                             headers={"Cache-Control": "no-store"})


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def app_shell(token: str = Query("")):
    """The shell is static: the token in the query string is consumed by the
    page's own script, stored once, and scrubbed from the URL. Nothing about
    the shop is rendered server-side, so this response is safe to be identical
    for everyone - and must not be cached with a token in the URL."""
    from app.routers.mobile_ui import APP_HTML
    return HTMLResponse(APP_HTML, headers={
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
