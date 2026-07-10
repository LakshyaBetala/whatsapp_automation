"""Reminder batches - up to 5 per business.

A batch bundles the reminder SEVERITY (style), LANGUAGE, early-pay DISCOUNT and
CUSTOM LINE. Each party (client) is assigned to a batch via clients.reminder_batch
(an index into businesses.reminder_batches; 0 = the first/default batch). The
reminder sweep and Send Now resolve a party's batch and use its settings instead
of one global setting - so different customer groups can get different tone,
language and discount, with the message + QR reflecting the batch.

Backward compatible: a business with no batches configured falls back to a single
default batch built from its old global reminder_style / msg_language /
discount_pct / reminder_custom_line, so nothing breaks before migration 016 data
exists.
"""
from __future__ import annotations

MAX_BATCHES = 5
_STYLES = ("gentle", "standard", "firm")


def default_batch(biz: dict) -> dict:
    """The implicit batch 0 for a business that has not configured batches:
    mirrors the pre-batches global settings."""
    return {
        "name": "Standard",
        "style": (biz.get("reminder_style") or "standard"),
        "lang": (biz.get("msg_language") or "hinglish"),
        "disc": _num(biz.get("discount_pct")),
        "line": (biz.get("reminder_custom_line") or ""),
    }


def _num(v) -> float:
    try:
        return max(0.0, min(50.0, float(v or 0)))
    except (TypeError, ValueError):
        return 0.0


def normalize_batch(b: dict, biz: dict | None = None) -> dict:
    style = str((b or {}).get("style") or "standard").lower()
    if style not in _STYLES:
        style = "standard"
    lang = "english" if str((b or {}).get("lang") or "").lower() == "english" else "hinglish"
    name = (str((b or {}).get("name") or "").strip() or "Batch")[:24]
    return {
        "name": name,
        "style": style,
        "lang": lang,
        "disc": round(_num((b or {}).get("disc")), 2),
        "line": str((b or {}).get("line") or "").strip()[:120],
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
