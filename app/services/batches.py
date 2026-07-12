"""Reminder batches - up to 5 per business.

A batch is a customer GROUP with its own LANGUAGE (Hindi/English), UPI account,
SEND TIME (hour) and an optional early-pay DISCOUNT. The reminder + overdue copy
is automatic (one message per language) - there is no tone/severity to pick.
Each party (client) is assigned to a batch via clients.reminder_batch (an index
into businesses.reminder_batches; 0 = the first/default batch). The reminder
sweep and Send Now resolve a party's batch and use its settings.

Backward compatible: a business with no batches configured falls back to a single
default batch built from its old global msg_language / discount_pct /
reminder_hour, and any older saved batch that still carries style/line is read
without error (those fields are simply dropped on the next save).
"""
from __future__ import annotations


MAX_BATCHES = 5


def default_batch(biz: dict) -> dict:
    """The implicit batch 0 for a business that has not configured batches."""
    return {
        "name": "All parties",
        "lang": (biz.get("msg_language") or "hinglish"),
        "disc": _num(biz.get("discount_pct")),
        "upi": "",   # blank = use the shop's default UPI (businesses.upi_vpa)
        "hour": _hour(biz.get("reminder_hour")),
    }


def batch_vpa(biz: dict, batch: dict) -> str | None:
    """The UPI VPA a batch pays into: its own, else the shop default."""
    return (batch.get("upi") or "").strip() or (biz.get("upi_vpa") or None)


def batch_hour(biz: dict, batch: dict) -> int:
    """The hour (0-23) a batch sends reminders at; falls back to the business
    default (reminder_hour, else 11)."""
    h = batch.get("hour")
    if h is None or h == "":
        return _hour(biz.get("reminder_hour"))
    return _hour(h)


def _num(v) -> float:
    try:
        return max(0.0, min(50.0, float(v or 0)))
    except (TypeError, ValueError):
        return 0.0


def _hour(v, default: int = 11) -> int:
    try:
        h = int(v)
    except (TypeError, ValueError):
        return default
    return h if 0 <= h <= 23 else default


def normalize_batch(b: dict, biz: dict | None = None) -> dict:
    lang = "english" if str((b or {}).get("lang") or "").lower() == "english" else "hinglish"
    name = (str((b or {}).get("name") or "").strip() or "Batch")[:24]
    return {
        "name": name,
        "lang": lang,
        "disc": round(_num((b or {}).get("disc")), 2),
        "upi": str((b or {}).get("upi") or "").strip()[:60],
        "hour": _hour((b or {}).get("hour")),
    }


def get_batches(biz: dict) -> list[dict]:
    """Normalized batch list for a business (always at least one)."""
    raw = biz.get("reminder_batches") or []
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    out = [normalize_batch(b, biz) for b in raw[:MAX_BATCHES] if isinstance(b, dict)]
    return out or [default_batch(biz)]


def resolve_batch(biz: dict, idx) -> dict:
    """The batch a party belongs to (falls back to the first batch)."""
    batches = get_batches(biz)
    try:
        i = int(idx or 0)
    except (TypeError, ValueError):
        i = 0
    return batches[i] if 0 <= i < len(batches) else batches[0]


def normalize_batches(raw: list) -> list[dict]:
    """Clean a submitted batch list for saving (1..MAX_BATCHES)."""
    out = [normalize_batch(b) for b in (raw or [])[:MAX_BATCHES] if isinstance(b, dict)]
    return out or [normalize_batch({})]
