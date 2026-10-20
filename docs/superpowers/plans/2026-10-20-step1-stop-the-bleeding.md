# Step 1: Stop the Bleeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ASVA's reminder bookkeeping truthful, its reply reading accurate and cheap, its invoices private, its plans and trial enforced, and its website honest, then deploy.

**Architecture:** Small, independent changes to the existing FastAPI backend. Cadence markers get a `parent_message_id` and are settled by one helper (`app/services/markers.py`) wherever an outbox row reaches a final state. Reply reading becomes rules first (`app/services/reply_rules.py`, pure) then Gemini Flash-Lite. Plans change only in `PLAN_LIMITS`/`PLAN_LABELS`; the debtor cap is enforced inside the existing priority drip of `reminder_sweep`.

**Tech Stack:** Python 3.11, FastAPI, supabase-py, pytest, PostgreSQL (Supabase), bash (deploy), PowerShell (laptop backup).

Spec: `docs/superpowers/specs/2026-10-20-asva-reliability-and-smart-replies-design.md` (Step 1). Evidence: `docs/AUDIT_2026-10-20.md`.

Conventions: run tests from the repo root with `.venv/Scripts/python -m pytest`. Owner- or customer-facing text never contains em or en dashes. Every handler that does not `await` stays `def`.

---

## File map

| File | Change |
|---|---|
| `migrations/054_marker_parent.sql` | new: `messages.parent_message_id` + repair of false markers |
| `migrations/055_private_invoices.sql` | new: `invoices` bucket private |
| `app/services/markers.py` | new: `settle(db, message_db_id, delivered)` |
| `app/services/outbox.py` | call `markers.settle` in `_mark` and `_hold` |
| `app/jobs/outbox_sweep.py` | call `markers.settle` in `_mark` and `expire_stale` |
| `app/services/bot.py` | `_send_consolidated_reminder` records `db_id`/`queued` on `entry`; SENT count skips markers |
| `app/jobs/reminder_sweep.py` | `_marker_row` helper, queued markers with parent; debtor cap via `_covered_parties` |
| `app/services/monitoring.py` | counts skip markers |
| `app/routers/admin.py` | party timeline skips markers |
| `app/services/reply_rules.py` | new: preset rules |
| `app/services/intent.py` | Flash-Lite, no thinking, new intents, Tamil |
| `app/services/conversations.py` | `record` returns id; new `set_intent` |
| `app/services/replies.py` | rules first; new routing; store intent |
| `app/services/pdf.py` | private bucket, signed URLs |
| `app/models.py` | new plan limits and labels; bot on every plan |
| `app/config.py` | `trial_days = 7` |
| `app/services/license.py` | trial creation; `debtor_cap_notice` |
| `app/routers/license.py` | `months=0` means trial |
| `app/routers/ops.py` | plan labels; create form trial option |
| `app/jobs/subscription_check.py` | trial notices day 5 and 7; renew notices 7/3/1 |
| `app/jobs/eod_digest.py` | debtor cap line |
| `app/site.py` | pricing and stale pilot copy |
| `deploy/linux/bootstrap.sh` | `node index.js` |
| `deploy/pull_backups.ps1` | new: laptop backup pull |
| tests | `test_markers.py`, `test_reply_rules.py` new; others extended |

---

### Task 1: Migration and the marker settle helper

**Files:**
- Create: `migrations/054_marker_parent.sql`
- Create: `app/services/markers.py`
- Test: `test_markers.py`

- [ ] **Step 1: Write the migration**

```sql
-- 054_marker_parent.sql
-- A cadence marker records "this bill's reminder for cadence day N is taken".
-- It used to be written as 'sent' the moment the real message was QUEUED, so a
-- message that later expired (shop laptop off) left markers claiming a reminder
-- that never went out (894 markers, 380 customers on 20 Oct 2026).
-- Markers now point at the real message and follow its fate.
alter table public.messages
  add column if not exists parent_message_id uuid;

create index if not exists idx_messages_parent
  on public.messages (parent_message_id)
  where parent_message_id is not null;

-- One-off repair: drop markers whose real reminder (same customer, written in
-- the same second burst, not itself a marker) expired or failed, so those
-- customers become due again. The daily caps spread the resends over days.
delete from public.messages m
 where m.template_name = 'cadence_marker'
   and m.parent_message_id is null
   and exists (
     select 1 from public.messages r
      where r.client_id = m.client_id
        and r.type = 'reminder'
        and coalesce(r.template_name, '') <> 'cadence_marker'
        and r.delivery_status in ('expired', 'failed')
        and abs(extract(epoch from (r.created_at - m.created_at))) < 5);
```

- [ ] **Step 2: Write the failing test**

```python
# test_markers.py
"""Cadence markers follow the real message they stand for."""
from app.services import markers


class _Q:
    def __init__(self, calls, name):
        self.calls, self.name, self.ops = calls, name, []
    def update(self, patch): self.ops.append(("update", patch)); return self
    def delete(self): self.ops.append(("delete",)); return self
    def eq(self, k, v): self.ops.append(("eq", k, v)); return self
    def execute(self):
        self.calls.append((self.name, self.ops))
        return type("R", (), {"data": []})()


class FakeDB:
    def __init__(self): self.calls = []
    def table(self, name): return _Q(self.calls, name)


def test_delivered_flips_markers_to_sent():
    db = FakeDB()
    markers.settle(db, "m1", delivered=True)
    (name, ops), = db.calls
    assert name == "messages"
    assert ("update", {"delivery_status": "sent"}) in ops
    assert ("eq", "parent_message_id", "m1") in ops
    assert ("eq", "template_name", "cadence_marker") in ops


def test_not_delivered_deletes_markers_so_the_point_retries():
    db = FakeDB()
    markers.settle(db, "m1", delivered=False)
    (name, ops), = db.calls
    assert ("delete",) in ops and ("eq", "parent_message_id", "m1") in ops


def test_no_parent_id_does_nothing():
    db = FakeDB()
    markers.settle(db, None, delivered=False)
    assert db.calls == []


def test_db_error_never_raises():
    class Boom:
        def table(self, name): raise RuntimeError("down")
    markers.settle(Boom(), "m1", delivered=True)   # must not raise
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest test_markers.py -q`
Expected: FAIL, `ImportError: cannot import name 'markers'`

- [ ] **Step 4: Write the helper**

```python
# app/services/markers.py
"""Cadence markers follow the real message they stand for.

The reminder sweep writes one marker per (bill, cadence day) so a point never
fires twice. When the real message is only QUEUED (it waits for a WhatsApp to
deliver it), the marker is written 'queued' with parent_message_id = the real
message. When the outbox row reaches a final state, settle() is called:
delivered -> markers become 'sent'; expired / failed / held -> markers are
deleted, so the cadence point is due again at the next sweep. Best-effort: a
failure here must never break a delivery or an ack.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MARKER = "cadence_marker"


def settle(db, message_db_id, delivered: bool) -> None:
    if not message_db_id:
        return
    try:
        q = db.table("messages")
        q = q.update({"delivery_status": "sent"}) if delivered else q.delete()
        q.eq("parent_message_id", message_db_id).eq("template_name", MARKER).execute()
    except Exception:
        log.warning("marker settle failed for message %s", message_db_id, exc_info=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest test_markers.py -q`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add migrations/054_marker_parent.sql app/services/markers.py test_markers.py
git commit -m "Markers follow their real message: migration 054 + settle helper"
```

---

### Task 2: Settle markers wherever an outbox row ends

**Files:**
- Modify: `app/services/outbox.py` (`_hold`, `_mark`)
- Modify: `app/jobs/outbox_sweep.py` (`expire_stale`, `_mark`)
- Test: `test_markers.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test_markers.py`:

```python
class _Upd:
    """Fake for outbox._mark: update(...).eq(...).eq(...).execute() returns the row."""
    def __init__(self, row): self.row = row
    def update(self, patch): return self
    def eq(self, *a): return self
    def execute(self): return type("R", (), {"data": [self.row]})()


class OutboxDB:
    def __init__(self, row): self.row = row
    def table(self, name): return _Upd(self.row)


def _record(monkeypatch):
    seen = []
    monkeypatch.setattr(markers, "settle", lambda db, mid, delivered: seen.append((mid, delivered)))
    return seen


def test_laptop_ack_sent_settles_delivered(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    monkeypatch.setattr(outbox, "_cleanup_pdf", lambda db, mid: None)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "sent")
    assert seen == [("m1", True)]


def test_laptop_ack_failed_settles_not_delivered(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "failed")
    assert seen == [("m1", False)]


def test_laptop_ack_still_queued_settles_nothing(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox.ack(OutboxDB({"id": "o1", "message_db_id": "m1"}), "b1", "o1", "queued")
    assert seen == []


def test_held_reminder_releases_its_markers(monkeypatch):
    from app.services import outbox
    seen = _record(monkeypatch)
    outbox._hold(OutboxDB({}), "b1", "o1", "m1")
    assert seen == [("m1", False)]


def test_server_sweep_mark_settles(monkeypatch):
    from app.jobs import outbox_sweep
    seen = _record(monkeypatch)
    monkeypatch.setattr(outbox_sweep, "_cleanup_pdf", lambda db, mid: None)
    db = OutboxDB({})
    outbox_sweep._mark(db, {"id": "o1", "message_db_id": "m1"}, "sent", 1, None)
    outbox_sweep._mark(db, {"id": "o2", "message_db_id": "m2"}, "failed", 3, "x")
    assert seen == [("m1", True), ("m2", False)]


def test_stale_expiry_releases_markers(monkeypatch):
    import asyncio
    from app.jobs import outbox_sweep
    seen = _record(monkeypatch)

    class _Sel(_Upd):
        def select(self, *a): return self
        def lt(self, *a): return self
        def limit(self, *a): return self
    class DB:
        def table(self, name): return _Sel({"id": "o1", "business_id": "b1", "message_db_id": "m1", "attempts": 0})
    monkeypatch.setattr("app.db.get_client", lambda: DB())
    asyncio.run(outbox_sweep.expire_stale())
    assert ("m1", False) in seen
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test_markers.py -q`
Expected: the 6 new tests FAIL (`seen == []`)

- [ ] **Step 3: Wire `outbox.py`**

At the top of `app/services/outbox.py`, change the import line:

```python
from app.services import markers, promises
```

In `_hold`, after the `messages` update inside the `try` (after line `db.table("messages").update({"delivery_status": "held"})...`), add:

```python
        markers.settle(db, message_db_id, delivered=False)
```

In `_mark`, after the `if status in ("sent", "failed") and row.get("message_db_id"):` block, add:

```python
    if status in ("sent", "failed"):
        markers.settle(db, row.get("message_db_id"), delivered=status == "sent")
```

- [ ] **Step 4: Wire `outbox_sweep.py`**

Add to the imports of `app/jobs/outbox_sweep.py`:

```python
from app.services import markers
```

In `expire_stale`, inside the loop after the `messages` update for `expired`:

```python
            markers.settle(db, r.get("message_db_id"), delivered=False)
```

In `_mark`, after the mirror block:

```python
    if status in ("sent", "failed"):
        markers.settle(db, row.get("message_db_id"), delivered=status == "sent")
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_markers.py test_outbox.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/services/outbox.py app/jobs/outbox_sweep.py test_markers.py
git commit -m "Outbox final states settle their cadence markers"
```

---

### Task 3: The sweep writes queued markers with their parent

**Files:**
- Modify: `app/services/bot.py` (`_send_consolidated_reminder`, end of function)
- Modify: `app/jobs/reminder_sweep.py` (marker insert, around line 576)
- Test: `test_markers.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_marker_row_for_a_queued_message():
    from app.jobs.reminder_sweep import _marker_row
    row = _marker_row("b1", "c1", "bill1", 15, parent_id="m9", queued=True)
    assert row["template_name"] == "cadence_marker"
    assert row["delivery_status"] == "queued" and row["parent_message_id"] == "m9"
    assert row["bill_id"] == "bill1" and row["reminder_day"] == 15 and row["type"] == "reminder"


def test_marker_row_for_a_direct_send():
    from app.jobs.reminder_sweep import _marker_row
    row = _marker_row("b1", "c1", "bill1", 15, parent_id=None, queued=False)
    assert row["delivery_status"] == "sent" and "parent_message_id" not in row
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_markers.py -q -k marker_row`
Expected: FAIL, `ImportError: cannot import name '_marker_row'`

- [ ] **Step 3: Expose the real message on `entry`**

In `app/services/bot.py`, `_send_consolidated_reminder`, replace the three-line result handling at the end:

```python
    # The sweep links its cadence markers to the real message (markers.settle).
    entry["db_id"] = result.get("db_id")
    entry["queued"] = bool(result.get("queued"))
    if result.get("queued"):
        return True, f"{name}: {inr(total)} ({len(bills)} bills) - in queue, goes from your shop number"
    if result.get("sent"):
        return True, f"{name}: {inr(total)} ({len(bills)} bills) - sent"
    return False, f"{name}: {_send_fail_note(result)}"
```

- [ ] **Step 4: Add `_marker_row` and use it**

In `app/jobs/reminder_sweep.py`, after `_party_priority`, add:

```python
def _marker_row(biz_id: str, client_id: str, bill_id: str, day: int, *,
                parent_id: str | None, queued: bool) -> dict:
    """One cadence marker. A queued real message makes a 'queued' marker that
    points at it; markers.settle() flips it to 'sent' on delivery or deletes it
    on expiry, so a reminder that never went out is retried."""
    row = {
        "business_id": biz_id, "client_id": client_id, "bill_id": bill_id,
        "type": MessageType.reminder.value, "reminder_day": day,
        "template_name": "cadence_marker", "language": "hi",
        "delivery_status": "queued" if queued else "sent", "cost": 0,
    }
    if parent_id and queued:
        row["parent_message_id"] = parent_id
    return row
```

In the sweep loop, before the `if biz.get(...)` escalation / customer branch (where `ok` is first set), add `parent_id, queued = None, False`. In the customer branch, right after `ok, line = await bot_svc._send_consolidated_reminder(biz, entry)`, add:

```python
                parent_id, queued = entry.get("db_id"), entry.get("queued", False)
```

Replace the marker insert body:

```python
                for bill, day, kind in triggers:
                    try:
                        db.table("messages").insert(_marker_row(
                            biz_id, client_id, bill["id"], day,
                            parent_id=parent_id, queued=queued)).execute()
                    except Exception:
                        log.exception("Marker insert failed for bill %s", bill["id"])
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_markers.py test_sweep_consolidated.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/services/bot.py app/jobs/reminder_sweep.py test_markers.py
git commit -m "Sweep writes queued markers linked to the real message"
```

---

### Task 4: Every count skips markers

**Files:**
- Modify: `app/services/monitoring.py:120-139`
- Modify: `app/services/bot.py` (`_handle_sent_today`, around line 1873)
- Modify: `app/routers/admin.py` (party timeline, around line 3008)
- Test: `test_monitoring.py` (append)

- [ ] **Step 1: Write the failing test**

Look at how `test_monitoring.py` builds its fake db for `build_health` (rows per table). Append:

```python
def test_markers_are_not_counted_as_sent(monkeypatch):
    import datetime as _dt
    from app.services import monitoring
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    rows = {
        "messages": [
            {"business_id": "a", "delivery_status": "sent", "created_at": now, "template_name": "manual_remind_hi"},
            {"business_id": "a", "delivery_status": "sent", "created_at": now, "template_name": "cadence_marker"},
            {"business_id": "a", "delivery_status": "sent", "created_at": now, "template_name": "cadence_marker"},
        ],
    }
    monkeypatch.setattr(monitoring, "_paged", lambda q: rows.get(_table_of(q), []))
```

The existing tests already define how `_paged` is faked; reuse that fixture instead of `_table_of` if one exists. The assertion:

```python
    h = monitoring.build_health(fake_db_with(rows))   # same helper the file uses
    assert h["totals"]["sent_today"] == 1
    assert h["traffic"][-1]["sent"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_monitoring.py -q -k markers`
Expected: FAIL, `sent_today == 3`

- [ ] **Step 3: Skip markers in monitoring**

In `build_health`, both message loops select `template_name` and skip markers:

```python
    for m in _paged(lambda: db.table("messages").select("business_id, delivery_status, template_name")
                    .gte("created_at", day_start.isoformat())):
        if m.get("template_name") == "cadence_marker":
            continue
        bid, st = m.get("business_id"), m.get("delivery_status")
```

```python
    for m in _paged(lambda: db.table("messages").select("created_at, delivery_status, template_name")
                    .gte("created_at", span_start.isoformat())):
        if m.get("template_name") == "cadence_marker":
            continue
        d = _parse_ts(m.get("created_at"))
```

- [ ] **Step 4: Skip markers in SENT and the party timeline**

In `bot.py` `_handle_sent_today`, add `template_name` to the select and, in the loop over rows, `if r.get("template_name") == "cadence_marker": continue`.

In `admin.py` at the party timeline query (`select("type, created_at, delivery_status, reminder_day")`), add `template_name` to the select and filter: `msgs = [m for m in msgs if m.get("template_name") != "cadence_marker"]` right after the query.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_monitoring.py test_admin_render.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/services/monitoring.py app/services/bot.py app/routers/admin.py test_monitoring.py
git commit -m "Counts show real messages: cadence markers excluded"
```

---

### Task 5: Preset reply rules

**Files:**
- Create: `app/services/reply_rules.py`
- Test: `test_reply_rules.py`

- [ ] **Step 1: Write the failing test**

```python
# test_reply_rules.py
"""Preset rules settle common replies with no AI call."""
import pytest

from app.services.reply_rules import match


@pytest.mark.parametrize("text,intent", [
    ("Paid", "paid_claim"),
    ("payment done sir", "paid_claim"),
    ("paisa bhej diya", "paid_claim"),
    ("kattiten", "paid_claim"),
    ("Paid using Google Pay! ✅ Crores of Indians trust Google Pay", "paid_claim"),
    ("kitna baki hai bhai", "balance_query"),
    ("evlo balance", "balance_query"),
    ("statement bhejo", "statement_request"),
    ("send ledger", "statement_request"),
    ("bill bhejo", "bill_request"),
    ("invoice copy please", "bill_request"),
    ("UPI id do", "payment_info"),
    ("account number send", "payment_info"),
    ("Nill balance", "dispute"),
    ("not our pls check", "dispute"),
    ("bill galat hai", "dispute"),
    ("Okay", "chatter"),
    ("Okay sir", "chatter"),
    ("thank you", "chatter"),
    ("சரி", "chatter"),
    ("Welcome to *Shanti Group* 😊 We're happy to assist you. How can we help?", "auto_greeting"),
    ("Hi Rupeshrtc! I've connected you with our team. They will reply soon.", "auto_greeting"),
])
def test_rules(text, intent):
    v = match(text)
    assert v is not None and v["intent"] == intent and v["source"] == "rule"


@pytest.mark.parametrize("text", [
    "5 tareek ko dunga",            # a date: the model resolves it
    "payment nahi hua abhi",        # negation: not a paid claim
    "paid 20000 on monday but bill is wrong",   # mixed and long
    "",
])
def test_rules_defer_to_the_model(text):
    assert match(text) is None


def test_gpay_text_carries_the_amount():
    v = match("Paid ₹12,500 using Google Pay")
    assert v["intent"] == "paid_claim" and v["amount"] == 12500.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_reply_rules.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Write the rules**

```python
# app/services/reply_rules.py
"""Preset rules: settle the common customer replies with no AI call.

Returns the same verdict shape as intent.classify(), plus source="rule", or
None to hand the message to the model. A rule fires only on a short,
single-purpose message; anything long, mixed, negated, or carrying a date is
for the model. Payment-app share texts and business auto-greetings are the
exceptions: they are long but unmistakable.
"""
from __future__ import annotations

import re

MAX_WORDS = 12

_AUTO_GREETING = re.compile(
    r"^\W*(welcome to\b|thank(s| you) for (contacting|reaching|messaging|your message)"
    r"|we('| a)re (currently )?(away|closed|unavailable)|i('|\s+ha)ve connected you"
    r"|this is an automated)"
    r"|how (can|may) (we|i) (help|assist)",
    re.I)
_PAY_APP = re.compile(r"(paid|sent|payment successful|transaction successful).{0,40}"
                      r"(google pay|gpay|phonepe|paytm|bhim|upi)", re.I)
_RUPEES = re.compile(r"(?:₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)", re.I)
_NEGATION = re.compile(r"\b(not|no|nahi|nahin|nai|illa|illai|won'?t|cannot|can'?t)\b", re.I)
_DIGIT = re.compile(r"\d")

# Order matters: dispute before negation (its phrases contain "no" / "not").
_DISPUTE = re.compile(
    r"\b(nill?|nil) balance\b|\bno (dues|balance)\b|\bnothing (is )?pending\b"
    r"|\bnot (our|ours|mine|my)\b|\bwrong (bill|amount)\b|\bbill galat\b|\bgalat bill\b"
    r"|\b(hamara|mera) nahi\b", re.I)

_RULES: list[tuple[str, re.Pattern]] = [
    ("paid_claim", re.compile(
        r"^(paid|payment (done|sent|made|completed)|done|sent|transferred"
        r"|paisa bhej diya|bhej diya|kar diya|ho gaya|payment ho gaya|de diya"
        r"|kattiten|kattitten|anupitten|anuppitten|pathavle|moklavi didha"
        r"|already paid|all payment paid|full payment done)\b", re.I)),
    ("balance_query", re.compile(
        r"\b(kitna|kitne|baki|baaki|bakaya|balance|outstanding|pending amount|how much"
        r"|evlo|evvalavu|evvlo|ketla|kiti|बाकी|कितना|எவ்வளவு|பாக்கி)\b", re.I)),
    ("statement_request", re.compile(
        r"\b(statement|ledger|hisab|hisaab|khata|account copy|soa)\b", re.I)),
    ("bill_request", re.compile(
        r"\b(bill|invoice|bil)\b.*\b(bhejo|bhej do|send|copy|anuppu|anupunga|please|pls|do)\b"
        r"|\b(send|share)\b.*\b(bill|invoice)\b", re.I)),
    ("payment_info", re.compile(
        r"\b(upi|account (no|number|details)|bank details|ifsc|qr|gpay number|phonepe number"
        r"|kaise pay|how to pay|payment details)\b", re.I)),
    ("chatter", re.compile(
        r"^(ok|okay|okk|k|thanks|thank you|thanku|noted|sure|fine|haan|ha|ji|theek hai|thik hai"
        r"|sari|saringa|nandri|dhanyavad|dhanyavaad|aabhar|barabar|good morning|gm|hi|hello"
        r"|👍|🙏|सरि|சரி|சரிங்க|நன்றி|धन्यवाद|ठीक है)( sir| ji| bhai| anna)?[.!]*$", re.I)),
]


def _verdict(intent: str, amount=None) -> dict:
    return {"intent": intent, "amount": amount, "promise_date": None,
            "confidence": 1.0, "source": "rule"}


def _rupees(text: str):
    m = _RUPEES.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def match(text: str) -> dict | None:
    t = (text or "").strip()
    if not t:
        return None
    if _AUTO_GREETING.search(t):
        return _verdict("auto_greeting")
    if _PAY_APP.search(t):
        return _verdict("paid_claim", _rupees(t))
    if len(t.split()) > MAX_WORDS:
        return None
    if _DISPUTE.search(t):
        return _verdict("dispute")
    if _NEGATION.search(t) or _DIGIT.search(t):
        return None
    hits = [name for name, rx in _RULES if rx.search(t)]
    if len(hits) == 1:
        return _verdict(hits[0])
    return None
```

- [ ] **Step 4: Run tests and adjust patterns until every case passes**

Run: `.venv/Scripts/python -m pytest test_reply_rules.py -q`
Expected: all pass. If a case fails, fix the pattern, not the test (the test cases are the spec).

- [ ] **Step 5: Commit**

```bash
git add app/services/reply_rules.py test_reply_rules.py
git commit -m "Preset reply rules: common customer replies settled without AI"
```

---

### Task 6: Gemini Flash-Lite, no thinking, new intents, Tamil

**Files:**
- Modify: `app/services/intent.py`
- Test: `test_intent.py` (append), `test_intent_golden.py` (extend list)

- [ ] **Step 1: Write the failing tests**

```python
def test_new_intents_are_accepted():
    for it in ("balance_query", "statement_request", "bill_request", "payment_info",
               "order", "auto_greeting"):
        v = intent._parse(_resp({"intent": it, "confidence": 0.9}))
        assert v["intent"] == it


def test_payload_uses_flash_lite_without_thinking():
    assert intent.GEMINI_MODEL == "gemini-2.5-flash-lite"
    cfg = intent._payload("hello", __import__("datetime").date(2026, 10, 20))["generationConfig"]
    assert cfg["thinkingConfig"] == {"thinkingBudget": 0}


def test_prompt_covers_tamil_and_disputes():
    p = intent._prompt(__import__("datetime").date(2026, 10, 20))
    assert "Tamil" in p and "Nill balance" in p and "order" in p
```

(`_resp` is the helper `test_intent.py` already uses to wrap a JSON object in a Gemini response; if it is named differently, use that name.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test_intent.py -q`
Expected: 3 FAIL

- [ ] **Step 3: Change `intent.py`**

```python
GEMINI_MODEL = "gemini-2.5-flash-lite"
```

```python
INTENTS = {"paid_claim", "promise", "dispute", "balance_query", "statement_request",
           "bill_request", "payment_info", "order", "auto_greeting", "chatter", "unclear"}
```

In `RESPONSE_SCHEMA`, the `intent` enum becomes `sorted(INTENTS)`.

Replace `_prompt`:

```python
def _prompt(today: _dt.date) -> str:
    return (
        "You classify a customer's WhatsApp message to an Indian wholesale shop that "
        "sends them payment reminders. The customer may write in English, Hindi, "
        "Hinglish, Tamil, Tanglish (Tamil in Latin letters), Gujarati or Marathi, "
        f"often romanised. Today is {today.isoformat()} (Asia/Kolkata).\n\n"
        "Return:\n- intent, one of:\n"
        "  paid_claim = says they have already paid or are paying now (paid, done, "
        "paisa bhej diya, kar diya, kattiten, anupitten)\n"
        "  promise = will pay at a FUTURE time (kal, 5 tareek ko, next week, naalaiku)\n"
        "  dispute = says the amount or bill is wrong, says nothing is due, or refuses "
        "(\"Nill balance\", \"not ours pls check\", \"bill galat hai\")\n"
        "  balance_query = asks how much is due (kitna baki hai, evlo balance)\n"
        "  statement_request = asks for a statement, ledger or hisab\n"
        "  bill_request = asks for a copy of a bill or invoice\n"
        "  payment_info = asks for UPI ID, bank or account details, QR, how to pay\n"
        "  order = wants to buy goods or asks about stock or delivery "
        "(\"10 box wire bhejo\", \"rate kya hai\")\n"
        "  auto_greeting = an automatic reply from their own WhatsApp Business "
        "(\"Welcome to X\", \"Thanks for contacting\", away message)\n"
        "  chatter = greeting, thanks, ok, or anything else not about payment\n"
        "  unclear = about payment but you cannot tell which\n"
        "- amount: the rupee number they mention if any (numeric only), else null\n"
        "- promise_date: for a promise, the absolute date YYYY-MM-DD they mean; else null\n"
        "- confidence: 0 to 1\n"
        "Never guess an amount or date that is not implied. If unsure, use unclear "
        "with low confidence."
    )
```

Extract the payload into a function and use it in `classify`:

```python
def _payload(text: str, today: _dt.date) -> dict:
    return {
        "contents": [{"parts": [{"text": _prompt(today) + "\n\nMessage:\n" + text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
```

In `classify`, replace the inline `payload = {...}` with `payload = _payload(text, today)`.

- [ ] **Step 4: Extend the golden set**

Add to `GOLDEN` in `test_intent_golden.py`:

```python
    ("Nill balance", "dispute"),
    ("not our pls check", "dispute"),
    ("kitna baki hai bhai", "balance_query"),
    ("evlo balance pending", "balance_query"),
    ("bill copy anupunga", "bill_request"),
    ("UPI number kya hai", "payment_info"),
    ("10 box wire bhejo kal", "order"),
    ("naalaiku pay panren", "promise"),
    ("kattiten sir", "paid_claim"),
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_intent.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/services/intent.py test_intent.py test_intent_golden.py
git commit -m "Reply AI: Flash-Lite, no thinking, Tamil, balance/bill/pay-info/order/dispute intents"
```

---

### Task 7: Rules first, new routing, intent stored

**Files:**
- Modify: `app/services/conversations.py` (`record` returns id; add `set_intent`)
- Modify: `app/services/replies.py` (`capture_reply` text path)
- Test: `test_replies.py` (append), `test_conversations.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test_replies.py`:

```python
def test_rule_settles_without_calling_the_model(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "unclear", "confidence": 0.1})
    calls = []
    async def spy(text):
        calls.append(text)
        return None
    monkeypatch.setattr(replies.intent, "classify", spy)
    _run("kitna baki hai bhai")
    assert calls == []                      # the rule answered
    assert rec.owner and "balance" in rec.owner[0].lower()
    assert rec.created == []                # nothing paused


def test_nill_balance_is_a_dispute_not_a_pause(monkeypatch):
    rec = _setup(monkeypatch)
    _run("Nill balance")
    assert rec.created == []
    assert rec.owner and "did NOT act" in rec.owner[0]


def test_auto_greeting_and_order_are_silent(monkeypatch):
    rec = _setup(monkeypatch, verdict={"intent": "order", "confidence": 0.95,
                                       "amount": None, "promise_date": None})
    assert _run("Welcome to *Shanti Group* How can we help?") is False
    assert _run("10 box wire bhejo kal aur rate batao") is False
    assert rec.owner == [] and rec.created == []


def test_final_intent_is_stored(monkeypatch):
    rec = _setup(monkeypatch)
    stored = []
    monkeypatch.setattr(replies.conversations, "record", lambda *a, **k: "row1")
    monkeypatch.setattr(replies.conversations, "set_intent",
                        lambda db, rid, it: stored.append((rid, it)))
    _run("statement bhejo")
    assert stored == [("row1", "statement_request")]
```

Append to `test_conversations.py`:

```python
def test_record_returns_the_row_id_and_set_intent_updates():
    from app.services import conversations

    class Q:
        def __init__(self): self.ops = []
        def insert(self, row): self.ops.append(("insert", row)); return self
        def update(self, p): self.ops.append(("update", p)); return self
        def eq(self, *a): self.ops.append(("eq",) + a); return self
        def execute(self): return type("R", (), {"data": [{"id": "r1"}]})()
    q = Q()
    db = type("DB", (), {"table": lambda self, n: q})()
    assert conversations.record(db, "b1", "c1", "hi") == "r1"
    conversations.set_intent(db, "r1", "chatter")
    assert ("update", {"intent": "chatter"}) in q.ops and ("eq", "id", "r1") in q.ops
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test_replies.py test_conversations.py -q`
Expected: the new tests FAIL

- [ ] **Step 3: `conversations.py`**

Change `record` to return the id (keep everything else):

```python
def record(db, business_id: str, client_id: str | None, body: str, *,
           intent: str | None = None, from_number: str | None = None) -> str | None:
    """Store one inbound message and return its row id. Best-effort; a missing
    table or a write error is logged at debug and returns None."""
    if not business_id or not (body or "").strip():
        return None
    try:
        r = db.table("inbound_messages").insert({
            "business_id": business_id,
            "client_id": client_id,
            "from_number": (from_number or "")[:20] or None,
            "body": body[:1000],
            "intent": intent,
        }).execute()
        return (r.data or [{}])[0].get("id")
    except Exception:
        log.debug("inbound record skipped (business %s)", business_id, exc_info=True)
        return None


def set_intent(db, row_id: str | None, intent: str | None) -> None:
    """Fill in what the reply turned out to mean (rules or model). Best-effort."""
    if not (row_id and intent):
        return
    try:
        db.table("inbound_messages").update({"intent": intent}).eq("id", row_id).execute()
    except Exception:
        log.debug("inbound intent update skipped (%s)", row_id, exc_info=True)
```

- [ ] **Step 4: `replies.py` text path**

Add `reply_rules` to the services import line:

```python
from app.services import conversations, intent, names, promises, reply_rules, whatsapp
```

Keep the row id from the existing `conversations.record(...)` call:

```python
    row_id = None
    try:
        row_id = conversations.record(require_db(), business_id, client_id,
                                      "[image]" if _has_img else text,
                                      intent="image" if _has_img else None)
    except Exception:
        pass
```

After the keyword `PAID` fast-path block, replace from `# 3. Understand natural free text with Gemini` to the end of the function with:

```python
    # 3. Preset rules first (no AI call), then the model for the rest.
    verdict = reply_rules.match(text) or await intent.classify(text)
    if verdict is None:
        # Model off or failed: do not guess and do not reply. Fall through.
        return False

    kind = verdict["intent"]
    conf = verdict["confidence"]
    amount = verdict.get("amount")
    try:
        conversations.set_intent(require_db(), row_id, kind)
    except Exception:
        pass

    # Not about money owed: nothing to do (the owner sees it on their phone).
    if kind in ("chatter", "order", "auto_greeting", "opt_out"):
        return False

    # Asking for facts: the owner is told (step 3 answers these automatically
    # on the Recover plan).
    asks = {"balance_query": "their balance", "statement_request": "a statement",
            "bill_request": "a bill copy", "payment_info": "your payment details"}
    if kind in asks and conf >= settings.promise_confidence_threshold:
        await _notify_owner(business_id,
            f'{name} is asking for {asks[kind]}: "{text[:200]}". Reply to them, or send '
            f"REMIND {name} to send their full balance with the pay link.")
        return True

    # Confidence gate: dispute / unclear / low confidence -> owner decides, no pause.
    if kind in ("dispute", "unclear") or conf < settings.promise_confidence_threshold:
        await _notify_owner(business_id,
            f'{name} replied: "{text[:300]}". ASVA did NOT act on this (it needs your '
            f"eye). Reminders are unchanged. Message {name} yourself, or reply "
            f"CHASE {name} / STOP {name}.")
        return True
```

The existing `paid_claim` and `promise` blocks that follow stay exactly as they are.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_replies.py test_conversations.py test_reply_rules.py test_intent.py -q`
Expected: all pass (the old "chatter falls through" test keeps passing because chatter still returns False).

- [ ] **Step 6: Commit**

```bash
git add app/services/conversations.py app/services/replies.py test_replies.py test_conversations.py
git commit -m "Replies: rules first, disputes never pause, info asks reach the owner, intent stored"
```

---

### Task 8: Private invoices bucket

**Files:**
- Create: `migrations/055_private_invoices.sql`
- Modify: `app/services/pdf.py`
- Test: `test_pdf_delete.py` (append)

(Note: the spec's step 2 migration for `wa_transport` becomes `056`.)

- [ ] **Step 1: Migration**

```sql
-- 055_private_invoices.sql
-- Invoice PDFs carry customer names and amounts; nobody should read them by
-- link. ASVA now hands out short-lived signed URLs instead of public ones.
update storage.buckets set public = false where id = 'invoices';
```

- [ ] **Step 2: Write the failing test**

```python
def test_upload_returns_a_signed_url(monkeypatch):
    import asyncio, base64
    from app.services import pdf

    class Bucket:
        def upload(self, *a, **k): return None
        def create_signed_url(self, path, seconds):
            assert seconds == pdf.SIGNED_URL_SECONDS
            return {"signedURL": f"https://x/storage/v1/object/sign/invoices/{path}?token=t"}
        def get_public_url(self, path): raise AssertionError("public URL must not be used")
    class Storage:
        def from_(self, name): return Bucket()
        def get_bucket(self, name): return {}
    db = type("DB", (), {"storage": Storage()})()
    monkeypatch.setattr(pdf, "require_db", lambda: db)
    url = asyncio.run(pdf.upload_pdf_base64("b1", "INV1", base64.b64encode(b"%PDF").decode()))
    assert "/sign/" in url and "token=" in url
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_pdf_delete.py -q -k signed`
Expected: FAIL (`AttributeError: SIGNED_URL_SECONDS` or the public-URL assertion)

- [ ] **Step 4: Change `pdf.py`**

Add after `BUCKET_NAME`:

```python
SIGNED_URL_SECONDS = 3600   # long enough to fetch and send; never a permanent link
```

In `_ensure_bucket`, create the bucket private: `options={"public": False, "file_size_limit": 5 * 1024 * 1024}`.

Add:

```python
def _signed_url(db, file_path: str) -> str:
    res = db.storage.from_(BUCKET_NAME).create_signed_url(file_path, SIGNED_URL_SECONDS)
    return (res or {}).get("signedURL") or (res or {}).get("signedUrl") or ""
```

In `upload_pdf_base64`, replace the last line with `return _signed_url(db, file_path)`.
In `generate_invoice_pdf`, replace `public_url = db.storage.from_(BUCKET_NAME).get_public_url(file_path)` and its log/return with:

```python
    url = _signed_url(db, file_path)
    log.info("Invoice PDF uploaded: %s", file_path)
    return url
```

Update the module docstring's last line to "and returns a short-lived signed URL".

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_pdf_delete.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add migrations/055_private_invoices.sql app/services/pdf.py test_pdf_delete.py
git commit -m "Invoices bucket private; PDFs served by 1-hour signed URLs"
```

---

### Task 9: New plans and labels

**Files:**
- Modify: `app/models.py:76-91`, `app/models.py:146-151`
- Modify: `app/routers/ops.py:745` (labels in the Command Center JS)
- Modify: `app/services/bot.py:434-436` (assistant gate text)
- Test: `test_license.py` (update expectations), new assertions in `test_license.py`

- [ ] **Step 1: Write the failing test**

Append to `test_license.py`:

```python
def test_new_plan_table():
    from app.models import PLAN_LIMITS, PLAN_LABELS, Plan, recommend_plan, plan_has_bot
    assert [PLAN_LIMITS[p]["price"] for p in (Plan.starter, Plan.growth, Plan.pro)] == [699, 999, 1499]
    assert [PLAN_LIMITS[p]["debtors"] for p in (Plan.starter, Plan.growth, Plan.pro)] == [150, 400, 1000]
    assert [PLAN_LIMITS[p]["messages"] for p in (Plan.starter, Plan.growth, Plan.pro)] == [1200, 3200, 8000]
    assert [PLAN_LABELS[p] for p in (Plan.starter, Plan.growth, Plan.pro)] == ["Lite", "Collect", "Recover"]
    assert all(plan_has_bot(p) for p in Plan)
    assert recommend_plan(150) == Plan.starter and recommend_plan(151) == Plan.growth
    assert recommend_plan(401) == Plan.pro and recommend_plan(1001) == Plan.max
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_license.py -q -k new_plan_table`
Expected: FAIL

- [ ] **Step 3: Change the plan table**

Replace the comment block above `PLAN_LIMITS` and the dict:

```python
# Plans (October 2026): 7-day free trial on full features, then priced by
# ACTIVE DEBTORS (parties with a WhatsApp number and an open bill).
#   Lite     Rs699   -> 150 active debtors
#   Collect  Rs999   -> 400 active debtors   (same features as Lite)
#   Recover  Rs1499  -> 1000 active debtors  (+ AI replies, Recovery file, multi-company)
#   Custom   quoted  -> above 1000
# Every plan includes the owner assistant (bot), the pay link and payment
# reconciliation. Messages and daily caps are fair-use and ban-safety limits.
PLAN_LIMITS: dict[Plan, dict[str, int]] = {
    Plan.starter: {"debtors": 150, "messages": 1200, "clients": 1000000, "price": 699, "bot": 1, "daily_cap": 50},
    Plan.growth: {"debtors": 400, "messages": 3200, "clients": 1000000, "price": 999, "bot": 1, "daily_cap": 100},
    Plan.pro: {"debtors": 1000, "messages": 8000, "clients": 1000000, "price": 1499, "bot": 1, "daily_cap": 150},
    Plan.max: {"debtors": 5000, "messages": 40000, "clients": 1000000, "price": 0, "bot": 1, "daily_cap": 300},
}
```

```python
PLAN_LABELS: dict[Plan, str] = {
    Plan.starter: "Lite",
    Plan.growth: "Collect",
    Plan.pro: "Recover",
    Plan.max: "Custom",
}
```

Update the docstring of `plan_has_bot` to "Every current plan includes it; kept for Custom deals."

In `app/routers/ops.py` line 745: `PLABEL={starter:'Lite',growth:'Collect',pro:'Recover',max:'Custom'}`.

In `app/services/bot.py` around line 434, the gate stays (it reads `plan_has_bot`) but its text says "the ASVA assistant is not on your plan; message us to add it."

- [ ] **Step 4: Fix the heartbeat test that pinned old values**

In `test_license.py` line 53 change the expectation to `hb["plan_label"] == "Recover" and hb["price"] == 1499`. Leave `debtor_cap == 1000` (unchanged for `pro`).

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_license.py test_mobile.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/routers/ops.py app/services/bot.py test_license.py
git commit -m "Plans: Lite 699/150, Collect 999/400, Recover 1499/1000; assistant on every plan"
```

---

### Task 10: 7-day trial and trial/renewal notices

**Files:**
- Modify: `app/config.py` (add `trial_days`)
- Modify: `app/services/license.py` (`create_business`)
- Modify: `app/routers/license.py` (`CreateBizPayload.months` default and docs)
- Modify: `app/routers/ops.py` (create form: trial default)
- Modify: `app/jobs/subscription_check.py`
- Test: `test_license.py`, `test_subscription.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test_license.py`:

```python
def test_months_zero_creates_a_seven_day_trial_on_full_features():
    import datetime as dt
    rows = []
    class Q:
        def insert(self, row): rows.append(row); return self
        def execute(self): return type("R", (), {"data": [rows[-1]]})()
    db = type("DB", (), {"table": lambda self, n: Q()})()
    today = dt.date(2026, 10, 20)
    biz = lic.create_business(db, owner_name="A", whatsapp_number="9876543210",
                              business_name="X", plan="starter", months=0, today=today)
    assert biz["plan"] == "pro" and biz["subscription_status"] == "trial"
    assert biz["plan_expires_on"] == "2026-10-27"
```

(Check the real signature of `lic.create_business`; if `today` is not a parameter, add it as `today: Optional[_dt.date] = None` and keep the existing `today = today or _dt.date.today()` line.)

Append to `test_subscription.py`:

```python
def test_trial_notice_names_the_plan_by_debtors(monkeypatch):
    import asyncio
    from app.jobs import subscription_check as sc
    sent = []
    async def fake_notify(bid, text): sent.append(text)
    monkeypatch.setattr(sc.whatsapp, "notify_owner", fake_notify)
    monkeypatch.setattr(sc.subs, "free_pilot_active", lambda: False)
    monkeypatch.setattr(sc.subs, "days_left", lambda exp, today=None: 2)
    monkeypatch.setattr(sc.subs, "effective_status", lambda exp, today=None: "active")
    monkeypatch.setattr(sc, "active_debtor_count", lambda db, bid: 320)
    biz = {"id": "b1", "business_name": "Ram Traders", "plan": "pro",
           "subscription_status": "trial", "plan_expires_on": "2026-10-22"}
    class Q:
        def select(self, *a): return self
        def update(self, *a): return self
        def eq(self, *a): return self
        def execute(self): return type("R", (), {"data": [biz]})()
    monkeypatch.setattr(sc, "require_db", lambda: type("DB", (), {"table": lambda s, n: Q()})())
    asyncio.run(sc.run())
    assert sent and "Collect" in sent[0] and "999" in sent[0] and "320" in sent[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test_license.py test_subscription.py -q -k "trial"`
Expected: FAIL

- [ ] **Step 3: Implement the trial**

`app/config.py`, next to `subscription_grace_days`:

```python
    trial_days: int = 7           # new shops: full features for this many days
```

`app/services/license.py` `create_business`: replace the months validation and the row's plan/expiry with:

```python
    if months < 0 or months > 60:
        raise ValueError("months must be between 0 (trial) and 60.")
    trial = months == 0
    row = {
        "owner_name": (owner_name or "").strip() or "Owner",
        "business_name": (business_name or "").strip() or None,
        "whatsapp_number": wa,
        "plan": Plan.pro.value if trial else plan_v,
        "agent_token": generate_agent_token(),
        "license_key": generate_license_key(),
        "plan_expires_on": ((today + _dt.timedelta(days=settings.trial_days)) if trial
                            else renew_expiry(None, months, today)).isoformat(),
        "subscription_status": "trial" if trial else "active",
        "onboarding_status": "active",
    }
```

`app/routers/license.py` `CreateBizPayload`: `months: float = 0   # 0 = 7-day free trial; N = N paid 30-day cycles`.

`app/routers/ops.py`: in the create-business form, find the months input and make its default `0` with the label "Months (0 = 7-day free trial)".

- [ ] **Step 4: Implement the notices**

In `app/jobs/subscription_check.py`, import:

```python
from app.models import PLAN_LABELS, PLAN_LIMITS, recommend_plan
from app.services.license import active_debtor_count
```

Replace the 5-day heads-up block with:

```python
            if stored == "trial" and left in (2, 0):
                n = active_debtor_count(db, biz["id"])
                rec = recommend_plan(n)
                price = PLAN_LIMITS[rec].get("price", 0)
                when = "today" if left == 0 else "in 2 days"
                plan_line = (f"your plan is {PLAN_LABELS[rec]} at Rs {price:,} a month "
                             f"(covers {PLAN_LIMITS[rec]['debtors']} debtors)"
                             if price else "message us for a Custom plan")
                pay_line = subs.renewal_payment_line(rec.value)
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA free trial ends {when}. You have {n} active "
                    f"debtors, so {plan_line}." + (("\n\n" + pay_line) if pay_line else ""))
            elif live in ("trial", "active") and left in (7, 3, 1):
                await whatsapp.notify_owner(
                    biz["id"],
                    f"{name}: your ASVA plan ends in {left} day{'s' if left != 1 else ''}. "
                    f"Renew to keep reminders and bills running." + tail)
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test_license.py test_subscription.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/services/license.py app/routers/license.py app/routers/ops.py app/jobs/subscription_check.py test_license.py test_subscription.py
git commit -m "7-day free trial; trial-end notice names the plan by debtor count; renew notices 7/3/1"
```

---

### Task 11: Enforce the debtor cap

**Files:**
- Modify: `app/jobs/reminder_sweep.py` (after `ordered = sorted(...)`)
- Modify: `app/services/license.py` (add `debtor_cap_notice`)
- Modify: `app/jobs/eod_digest.py` (append the notice)
- Test: `test_sweep_consolidated.py` or `test_markers.py` (pure helper tests), `test_license.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_covered_parties_takes_top_n_per_business():
    from app.jobs.reminder_sweep import _covered_parties
    ordered = [(("a", "c1"), {}), (("a", "c2"), {}), (("a", "c3"), {}),
               (("b", "d1"), {}), (("b", "d2"), {})]
    covered = _covered_parties(ordered, {"a": 2, "b": 5})
    assert covered == {("a", "c1"), ("a", "c2"), ("b", "d1"), ("b", "d2")}


def test_debtor_cap_notice():
    from app.models import Plan
    from app.services.license import debtor_cap_notice
    assert debtor_cap_notice(380, Plan.growth) is None
    msg = debtor_cap_notice(520, Plan.growth)
    assert "520" in msg and "400" in msg and "120" in msg and "Recover" in msg
    assert "—" not in msg and "–" not in msg
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test_markers.py test_license.py -q -k "covered or cap_notice"`
Expected: FAIL

- [ ] **Step 3: Implement**

`app/jobs/reminder_sweep.py`, after `_marker_row`:

```python
def _covered_parties(ordered: list, caps: dict) -> set:
    """The plan's debtor cap: per business, the first `cap` parties of the
    priority order (biggest x oldest first). Parties beyond the cap are not
    messaged; the owner is told in the digest. cap <= 0 means no cap."""
    seen: dict = {}
    covered = set()
    for (biz_id, client_id), _p in ordered:
        n = seen.get(biz_id, 0)
        cap = caps.get(biz_id, 0)
        if cap <= 0 or n < cap:
            covered.add((biz_id, client_id))
        seen[biz_id] = n + 1
    return covered
```

Right after `ordered = sorted(...)`:

```python
    debtor_caps = {}
    for bid, b in businesses.items():
        try:
            debtor_caps[bid] = PLAN_LIMITS[Plan(b.get("plan") or "starter")]["debtors"]
        except (ValueError, KeyError):
            debtor_caps[bid] = PLAN_LIMITS[Plan.starter]["debtors"]
    covered = _covered_parties(ordered, debtor_caps)
```

In the loop, after the daily-cap check:

```python
            if (biz_id, client_id) not in covered:
                skipped += 1
                continue
```

Make sure `PLAN_LIMITS` and `Plan` are imported in `reminder_sweep.py` (`from app.models import MessageType, Plan, PLAN_LIMITS`).

`app/services/license.py`:

```python
def debtor_cap_notice(active: int, plan: Plan) -> str | None:
    """Owner-facing line when the shop has more active debtors than its plan
    covers. None when within the cap."""
    cap = int(PLAN_LIMITS[plan]["debtors"])
    if active <= cap:
        return None
    nxt = recommend_plan(active)
    tail = (f" {PLAN_LABELS[nxt]} covers {PLAN_LIMITS[nxt]['debtors']:,}."
            if nxt != plan and PLAN_LIMITS[nxt].get("price") else " Message us for a Custom plan.")
    return (f"You have {active:,} active debtors; your {PLAN_LABELS[plan]} plan covers "
            f"{cap:,}, so {active - cap:,} were not reminded.{tail}")
```

(import `recommend_plan`, `PLAN_LABELS` from `app.models` if not already.)

`app/jobs/eod_digest.py`: where the digest text is assembled for a business, append:

```python
    try:
        from app.services.license import active_debtor_count, debtor_cap_notice
        note = debtor_cap_notice(active_debtor_count(db, business_id),
                                 Plan(biz.get("plan") or "starter"))
        if note:
            lines += ["", note]
    except Exception:
        log.debug("debtor cap note skipped", exc_info=True)
```

(Use the digest's actual list/variable names for the message lines and business row.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/jobs/reminder_sweep.py app/services/license.py app/jobs/eod_digest.py test_markers.py test_license.py
git commit -m "Debtor cap enforced in the priority drip; owner told in the digest"
```

---

### Task 12: Honest website copy

**Files:**
- Modify: `app/site.py` (lines 473-476, 744-752, 773-774, 782, 999-1012, 1027-1029, 1103, 1364)
- Test: `test_site_copy.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# test_site_copy.py
"""The website never advertises an expired free pilot and shows real prices."""
import re
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app import site


def _html(resp):
    return resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)


def test_no_expired_pilot_dates_anywhere():
    src = open(site.__file__, encoding="utf-8").read()
    assert not re.search(r"(15|30) September", src)


def test_pricing_page_shows_the_three_plans():
    src = open(site.__file__, encoding="utf-8").read()
    for s in ("Lite", "Collect", "Recover", "699", "999", "1,499", "7-day free trial"):
        assert s in src, s
    assert "—" not in src and "–" not in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test_site_copy.py -q`
Expected: FAIL

- [ ] **Step 3: Rewrite the copy**

Promo bar (lines 473-476):

```python
    return f"""<a class="promo" href="/pricing">
  <span class="wrap"><span class="tag">7-day free trial</span>
  Try the full ASVA free for 7 days, then from <b>Rs 699 a month</b>.
  <span class="go">See plans &rarr;</span></span></a>
```

Home section (lines 744-752):

```python
 <section>
  <div class="sechead"><span class="eyebrow">Pricing</span>
   <h2>Free for 7 days, then from Rs 699 a month</h2>
   <p>Every plan has bills and reminders on your own WhatsApp, the pay link and payments
     back into Tally. You choose the plan by how many customers owe you.</p></div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px">
    <a class="btn btn-p" href="/download">Start your 7-day free trial</a>
    <a class="btn" href="/pricing">See plans</a>
  </div>
 </section>
```

JSON-LD offer (line 773-774):

```python
         "offers": {"@type": "AggregateOffer", "lowPrice": "699", "highPrice": "1499",
                    "priceCurrency": "INR",
                    "description": "7-day free trial, then Lite Rs 699, Collect Rs 999 or Recover Rs 1,499 a month"},
```

Home meta description (line 782): replace the last sentence with "7-day free trial, then from Rs 699 a month."

Pricing hero (lines 999-1012), replacing the `page-hero` section:

```python
 <section class="page-hero reveal">
  <span class="eyebrow">Pricing</span>
  <h1>Pay for the customers you chase, <span class="hl">not for features</span></h1>
  <p class="lede">Start with a 7-day free trial on the full ASVA. After that, pick the plan
    that covers your active debtors: customers with a WhatsApp number who still owe you.</p>
  <div class="plans" style="display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:26px">
   <div class="card"><h3>Lite</h3><p class="price">Rs 699 <small>a month</small></p>
    <p>Up to 150 active debtors. Bills and reminders on your WhatsApp, pay link, payments back into Tally, owner assistant.</p></div>
   <div class="card"><h3>Collect</h3><p class="price">Rs 999 <small>a month</small></p>
    <p>Up to 400 active debtors. Everything in Lite.</p></div>
   <div class="card"><h3>Recover</h3><p class="price">Rs 1,499 <small>a month</small></p>
    <p>Up to 1,000 active debtors. Adds automatic replies to customers, promise follow-up and multi-company.</p></div>
  </div>
  <p class="undernote" style="margin-top:14px">More than 1,000 debtors? Message us for a Custom plan.</p>
  <div style="margin-top:22px;display:flex;gap:12px;flex-wrap:wrap">
    <a class="btn btn-p" href="/download">Start your 7-day free trial</a>
    <a class="btn" href="{WA_TRY}">Talk to us on WhatsApp</a>
  </div>
 </section>
```

Band (line 1012-1013): `"Message us on WhatsApp and we will get you live in about five minutes. The first 7 days are free."`

Pricing meta (lines 1027-1029):

```python
        title="ASVA pricing | 7-day free trial, then from Rs 699 a month",
        description="ASVA plans: Lite Rs 699 (150 debtors), Collect Rs 999 (400), Recover Rs 1,499 (1,000). 7-day free trial. WhatsApp bills and payment reminders from your TallyPrime.",
        keywords="ASVA pricing, Tally WhatsApp reminder price, payment reminder software India price, receivables software for Tally",
```

Line 1103: `<p class="lede"><strong>7-day free trial, then from Rs 699 a month.</strong> One installer for the computer`

Line 1364 (terms): `<p>ASVA is a subscription service. We aim for high availability but do not guarantee`

Also grep the FAQ list in `site.py` for "free" and fix any answer that promises free use to a date.

- [ ] **Step 4: Run tests and render**

Run: `.venv/Scripts/python -m pytest test_site_copy.py -q`
Expected: pass. Then `.venv/Scripts/python -c "from app import site"` must import cleanly.

- [ ] **Step 5: Commit**

```bash
git add app/site.py test_site_copy.py
git commit -m "Website: real prices, 7-day free trial, no expired pilot dates"
```

---

### Task 13: Bot service runs node directly

**Files:**
- Modify: `deploy/linux/bootstrap.sh:232`

- [ ] **Step 1: Change the unit**

```bash
ExecStart=/usr/bin/node index.js
```

- [ ] **Step 2: Apply on the live box (no rebuild needed)**

```bash
ssh root@asva-vps "sed -i 's#^ExecStart=/usr/bin/npm start#ExecStart=/usr/bin/node index.js#' /etc/systemd/system/asva-bot.service && systemctl daemon-reload && systemctl restart asva-bot && sleep 8 && systemctl is-active asva-bot && journalctl -u asva-bot -n 5 --no-pager | tail -3"
```

Expected: `active`, and a log line `WhatsApp CONNECTED (Baileys). Ready.`

- [ ] **Step 3: Commit**

```bash
git add deploy/linux/bootstrap.sh
git commit -m "Bot service runs node directly (drops the idle npm wrapper)"
```

---

### Task 14: Laptop pulls the nightly backup

**Files:**
- Create: `deploy/pull_backups.ps1`
- Modify: `deploy/VPS_MIGRATION.md` (one section)

- [ ] **Step 1: Write the script**

```powershell
# deploy/pull_backups.ps1
# Copy the newest nightly database dump from the ASVA server to this laptop,
# over Tailscale, and keep the last 14. Safe to run any time; does nothing
# when the server is unreachable. Scheduled daily by Task Scheduler.
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:USERPROFILE 'Documents\ASVA-backups'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$remoteDir = '/home/server-asva/asva/deploy/linux/backups'
try {
    $newest = (ssh -o BatchMode=yes -o ConnectTimeout=15 server-asva@asva-vps "ls -1t $remoteDir/asva_db_*.sql.gz* 2>/dev/null | head -1").Trim()
} catch { Write-Output 'server unreachable, skipping'; exit 0 }
if (-not $newest) { Write-Output 'no dump on server yet'; exit 0 }
$name = Split-Path $newest -Leaf
$target = Join-Path $dest $name
if (-not (Test-Path $target)) {
    scp -q -o BatchMode=yes "server-asva@asva-vps:$newest" "$target.partial"
    Move-Item -Force "$target.partial" $target
    Write-Output "pulled $name"
}
Get-ChildItem $dest -Filter 'asva_db_*' | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 | Remove-Item -Force
```

- [ ] **Step 2: Run it once**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File deploy/pull_backups.ps1`
Expected: `pulled asva_db_<stamp>.sql.gz` (or "no dump on server yet" before the first 02:30 run; then trigger one with `ssh root@asva-vps systemctl start asva-backup.service` and run again).

- [ ] **Step 3: Schedule it (ask the founder first; it is a standing task on their laptop)**

```powershell
schtasks /Create /TN "ASVA backup pull" /SC DAILY /ST 10:00 /F /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\laksh\whatsapp-tally-saas\deploy\pull_backups.ps1"
```

- [ ] **Step 4: Document and commit**

Add to `deploy/VPS_MIGRATION.md` a short "Backups off the box" section: where dumps live on the server, that `pull_backups.ps1` keeps 14 on the laptop, and the UptimeRobot monitors (`/health` 200, `link.tryasva.com/api/wa/status` 401).

```bash
git add deploy/pull_backups.ps1 deploy/VPS_MIGRATION.md
git commit -m "Off-box backups: laptop pulls the nightly dump, keeps 14"
```

---

### Task 15: Ship

- [ ] **Step 1: Full test run**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: all pass (842 + the new tests)

- [ ] **Step 2: Apply migrations 054 and 055 (no local psql; use the venv's psycopg2)**

```bash
.venv/Scripts/python -I - <<'EOF'
env = dict(l.strip().split('=',1) for l in open('.env', encoding='utf-8') if '=' in l and not l.startswith('#'))
import psycopg2
c = psycopg2.connect(env['SUPABASE_DB_URL'].strip('"')); c.autocommit = True; cur = c.cursor()
for f in ("migrations/054_marker_parent.sql", "migrations/055_private_invoices.sql"):
    cur.execute(open(f, encoding="utf-8").read()); print(f, "applied, rows:", cur.rowcount)
cur.execute("select count(*) from messages m where template_name='cadence_marker' and exists (select 1 from messages r where r.client_id=m.client_id and r.template_name<>'cadence_marker' and r.type='reminder' and r.delivery_status in ('expired','failed') and abs(extract(epoch from r.created_at-m.created_at))<5)")
print("false markers left:", cur.fetchone()[0])
cur.execute("select public from storage.buckets where id='invoices'"); print("invoices public:", cur.fetchone()[0])
EOF
```

Expected: `false markers left: 0`, `invoices public: False`

- [ ] **Step 3: Deploy**

Run: `bash deploy/push_release.sh --test`
Expected: `{"status":"ok","version":"2.1.3",...}` twice and `DONE`.

- [ ] **Step 4: Verify live**

```bash
curl -s https://app.tryasva.com/health
curl -s https://tryasva.com/pricing | grep -o "7-day free trial" | head -1
ssh server-asva@asva-vps 'cd ~/asva && .venv/bin/python - <<"EOF"
import asyncio, time
from app.services import intent, reply_rules
async def main():
    for t in ["Nill balance", "kitna baki hai bhai", "10 box wire bhejo kal", "naalaiku pay panren"]:
        s = time.time(); v = reply_rules.match(t) or await intent.classify(t)
        print(t, "->", v["intent"] if v else None, f"{time.time()-s:.2f}s")
asyncio.run(main())
EOF'
```

Expected: `dispute`, `balance_query`, `order`, `promise`, each under 1.5 s (rules near 0 s).

- [ ] **Step 5: Commit any fixes, update memory, report**
