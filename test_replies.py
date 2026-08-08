"""Reply-capture pipeline (capture_reply).

Covers every branch: keyword PAID (+amount), a confident promise (with date),
a confident paid_claim, the confidence gate (dispute / low confidence forward to
the owner, no hold), chatter falls through, Gemini-off degrades to silent, and a
screenshot forwards proof + holds. Everything at the boundary is mocked, so no
network and no DB.
"""
import asyncio
import datetime as dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import replies

CLIENT = {"id": "c1", "name": "Ramesh Traders", "business_id": "b1"}
FUTURE = (dt.date.today() + dt.timedelta(days=11)).isoformat()


class Rec:
    def __init__(self):
        self.created = []
        self.owner = []
        self.proof = []


def _setup(monkeypatch, verdict=None):
    rec = Rec()
    monkeypatch.setattr(replies, "require_db", lambda: object())

    def fake_create(db, bid, cid, **kw):
        rec.created.append({"business_id": bid, "client_id": cid, **kw})
        return {"id": "p1"}
    monkeypatch.setattr(replies.promises, "create", fake_create)

    async def fake_notify(bid, text):
        rec.owner.append(text)
    monkeypatch.setattr(replies.whatsapp, "notify_owner", fake_notify)

    async def fake_classify(text):
        return verdict
    monkeypatch.setattr(replies.intent, "classify", fake_classify)

    async def fake_proof(bid, name, mb, mt, caption):
        rec.proof.append((bid, name, caption))
    monkeypatch.setattr(replies, "_forward_proof", fake_proof)

    # Default: OCR reads nothing (tests that need a value override this). Keeps
    # every test off the live Gemini API. The screenshot path calls
    # ocr.extract_payment (amount + payer + ref + confidence).
    import app.services.ocr as _ocr
    async def _fake_pay(b64, mt="image/jpeg"):
        return None
    monkeypatch.setattr(_ocr, "extract_payment", _fake_pay)
    return rec


def _run(text, **kw):
    return asyncio.run(replies.capture_reply(CLIENT, text, **kw))


# ── keyword fast-path ───────────────────────────────────────────────────────
def test_keyword_paid_holds_and_notifies(monkeypatch):
    rec = _setup(monkeypatch)
    out = _run("PAID")
    assert rec.created and rec.created[0]["kind"] == "paid_claim"
    assert rec.created[0]["source"] == "keyword"
    assert rec.owner and "CHASE Ramesh Traders" in rec.owner[0]
    assert "replied to Ramesh Traders" in rec.owner[0]   # confirms we did NOT reply to the customer
    assert out is True                                    # acted -> bot stays silent to the customer


def test_keyword_paid_with_amount(monkeypatch):
    rec = _setup(monkeypatch)
    _run("PAID 20000")
    assert rec.created[0]["amount"] == 20000.0


def test_paid_with_amount_queues_a_receipt_in_payments_tab(monkeypatch):
    # A known customer reporting an amount lands in the owner's Payments tab,
    # party matched by their number, ready to confirm + post to Tally.
    rec = _setup(monkeypatch)
    queued = []
    monkeypatch.setattr(replies.rq, "create_pending",
                        lambda db, bid, **kw: queued.append(kw) or {"id": "r1"})
    client = {"id": "c1", "name": "Ramesh Traders", "business_id": "b1",
              "tally_ledger_name": "RAMESH TRADERS"}
    out = asyncio.run(replies.capture_reply(client, "PAID 5000"))
    assert out is True
    assert queued and queued[0]["amount"] == 5000.0
    assert queued[0]["party_ledger"] == "RAMESH TRADERS"
    assert queued[0]["client_id"] == "c1"
    assert rec.owner and "Payments tab" in rec.owner[0]


def test_keyword_paid_with_k_suffix(monkeypatch):
    rec = _setup(monkeypatch)
    _run("paid 20k")
    assert rec.created[0]["amount"] == 20000.0


# ── Gemini-classified branches ──────────────────────────────────────────────
def test_confident_promise_holds_with_date(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "promise", "amount": None,
                                       "promise_date": FUTURE, "confidence": 0.9})
    out = _run("5 tareek ko de dunga")
    assert rec.created[0]["kind"] == "promise"
    assert rec.created[0]["promise_date"] == FUTURE
    assert FUTURE in rec.owner[0]
    assert out is True


def test_confident_paid_claim(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "paid_claim", "amount": 5000.0,
                                       "promise_date": None, "confidence": 0.85})
    _run("paisa bhej diya")
    assert rec.created[0]["kind"] == "paid_claim" and rec.created[0]["amount"] == 5000.0


def test_dispute_forwards_to_owner_no_hold(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "dispute", "amount": None,
                                       "promise_date": None, "confidence": 0.9})
    out = _run("bill galat hai")
    assert rec.created == []                      # NOT auto-held
    assert rec.owner and "needs your eye" in rec.owner[0]
    assert out is True                            # owner nudged -> bot silent to customer


def test_low_confidence_forwards_no_hold(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "paid_claim", "amount": None,
                                       "promise_date": None, "confidence": 0.3})
    out = _run("hmm maybe paid")
    assert rec.created == [] and out is True
    assert rec.owner and "needs your eye" in rec.owner[0]


def test_chatter_falls_through(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "chatter", "amount": None,
                                       "promise_date": None, "confidence": 0.9})
    assert _run("good morning ji") is False       # falls through, nothing acted on
    assert rec.created == [] and rec.owner == []


def test_gemini_off_degrades_to_silent(monkeypatch):
    rec = _setup(monkeypatch, verdict=None)      # classify returns None
    assert _run("some random message") is False  # not PAID -> nothing to act on
    assert rec.created == []


# ── screenshot ──────────────────────────────────────────────────────────────
def test_screenshot_forwards_proof_and_holds(monkeypatch):
    rec = _setup(monkeypatch)
    out = _run("", media_b64="ZmFrZQ==", media_type="image/jpeg")
    assert rec.proof and rec.proof[0][0] == "b1" and rec.proof[0][1] == "Ramesh Traders"
    assert rec.created and rec.created[0]["source"] == "screenshot"
    # ONE owner message: the screenshot image carries the full caption; there is
    # no separate text nudge (that was the duplicate).
    assert "screenshot" in rec.proof[0][2].lower()
    assert rec.owner == []
    assert out is True


def test_screenshot_with_readable_amount_queues_a_receipt(monkeypatch):
    # When ASVA can read the amount off the screenshot, it goes to the Payments tab.
    import app.services.ocr as ocr
    rec = _setup(monkeypatch)
    async def fake_pay(b64, mt="image/jpeg"):
        return ocr.PaymentExtract(amount=310.0, payer="Ramesh", ref="123456789012", confidence=0.95)
    monkeypatch.setattr(ocr, "extract_payment", fake_pay)
    queued = []
    monkeypatch.setattr(replies.rq, "create_pending",
                        lambda db, bid, **kw: queued.append(kw) or {"id": "r1"})
    client = {"id": "c1", "name": "Ramesh Traders", "business_id": "b1",
              "tally_ledger_name": "RAMESH TRADERS"}
    out = asyncio.run(replies.capture_reply(client, "", media_b64="ZmFrZQ==", media_type="image/jpeg"))
    assert out is True
    assert queued and queued[0]["amount"] == 310.0
    # Amount read -> receipt queued, and the single image caption says so.
    assert rec.proof and "Payments tab" in rec.proof[0][2]
    assert rec.owner == []


def test_capture_reply_never_returns_a_customer_message(monkeypatch):
    """The safety property: ASVA never speaks to the customer. Every branch
    returns a bool (act / fall-through), never a string to send back."""
    verdicts = [
        None,
        {"intent": "chatter", "amount": None, "promise_date": None, "confidence": 0.9},
        {"intent": "promise", "amount": None, "promise_date": FUTURE, "confidence": 0.9},
        {"intent": "paid_claim", "amount": None, "promise_date": None, "confidence": 0.9},
        {"intent": "dispute", "amount": None, "promise_date": None, "confidence": 0.9},
        {"intent": "unclear", "amount": None, "promise_date": None, "confidence": 0.2},
    ]
    for v in verdicts:
        _setup(monkeypatch, verdict=v)
        assert isinstance(_run("kuch bhi likha hai"), bool)
    _setup(monkeypatch)
    assert isinstance(_run("PAID 5000"), bool)
    _setup(monkeypatch)
    assert isinstance(_run("", media_b64="ZmFrZQ=="), bool)


def test_hold_is_capped(monkeypatch):
    """A promise far in the future is capped at promise_max_hold_days."""
    far = (dt.date.today() + dt.timedelta(days=365)).isoformat()
    rec = _setup(monkeypatch, verdict={"intent": "promise", "amount": None,
                                       "promise_date": far, "confidence": 0.9})
    _run("will pay next year")
    hold = rec.created[0]["hold_until"]          # a timezone-aware datetime
    cap = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=replies.settings.promise_max_hold_days + 1)
    assert hold < cap                            # never silences longer than the cap
