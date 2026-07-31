"""Subscription lifecycle - server-side license enforcement.

The installed agent/exe is a dumb pipe; everything of value (scheduler,
sends, data) runs on our backend. So "license enforcement" is simply:
compute the subscription state from plan_expires_on on every send and
block when suspended. Copying the exe elsewhere gains nothing - the
agent_token maps to one business, and that business's clock is here.

States (grace period = settings.subscription_grace_days, default 3):
    trial/active : sends allowed
    grace        : expiry passed < GRACE_DAYS ago - sends allowed, owner warned
    suspended    : expiry passed >= GRACE_DAYS ago - customer sends BLOCKED
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from urllib.parse import quote

from app.config import settings
from app.models import PLAN_LABELS, PLAN_LIMITS, Plan

# Days of grace after expiry before suspension. Set once at startup from
# ADMIN/config; "pay -> keep access, lapse -> short grace, then cut off".
GRACE_DAYS = max(0, int(settings.subscription_grace_days))


def free_pilot_active(today: Optional[date] = None) -> bool:
    """True while the global free pilot is on (today <= settings.free_pilot_until).
    During the pilot every business is Pro + active: no suspension, no plan-limit
    block, no renewal nagging. One switch, set in config."""
    raw = (settings.free_pilot_until or "").strip()
    if not raw:
        return False
    try:
        until = date.fromisoformat(raw[:10])
    except ValueError:
        return False
    return (today or date.today()) <= until


def live_status(plan_expires_on: Optional[str | date], today: Optional[date] = None) -> str:
    """The status the SEND path should enforce: always 'active' during the free
    pilot, otherwise the real per-business status. Use this (not effective_status)
    wherever a send/reminder is gated, so the pilot never suspends anyone."""
    if free_pilot_active(today):
        return "active"
    return effective_status(plan_expires_on, today)


def effective_status(plan_expires_on: Optional[str | date], today: Optional[date] = None) -> str:
    """Live status from the expiry date - correct even if the daily job
    hasn't run (the stored subscription_status column is for display)."""
    today = today or date.today()
    if not plan_expires_on:
        return "active"  # no expiry set = legacy/internal business
    expiry = plan_expires_on if isinstance(plan_expires_on, date) else date.fromisoformat(str(plan_expires_on))
    if today <= expiry:
        return "active"
    if (today - expiry).days < GRACE_DAYS:
        return "grace"
    return "suspended"


def days_left(plan_expires_on: Optional[str | date], today: Optional[date] = None) -> Optional[int]:
    if not plan_expires_on:
        return None
    today = today or date.today()
    expiry = plan_expires_on if isinstance(plan_expires_on, date) else date.fromisoformat(str(plan_expires_on))
    return (expiry - today).days


def _plan_price(plan_value: Optional[str]) -> tuple[Plan, int]:
    try:
        plan = Plan(plan_value or "starter")
    except ValueError:
        plan = Plan.starter
    return plan, int(PLAN_LIMITS[plan].get("price", 0))


def renewal_payment_line(plan_value: Optional[str]) -> str:
    """The 'how to renew' block appended to a renewal notice. Degrades gracefully
    to '' when nothing is configured.

    The pay link is an https URL to our /pay page (WhatsApp makes https tappable;
    it does NOT linkify raw upi:// schemes). Tapping /pay opens the owner's UPI
    app with ASVA's UPI id and the exact amount prefilled - so 'renew' actually
    starts the payment. The UPI id stays as copyable text, and a wa.me contact
    link lets them reach us if they'd rather pay another way."""
    upi = (settings.operator_upi_id or "").strip()
    team = "".join(ch for ch in (settings.product_team_number or "") if ch.isdigit())
    base = (settings.public_base_url or "").rstrip("/")
    plan, price = _plan_price(plan_value)
    lines: list[str] = []
    if upi and base:
        # Tappable https -> opens the UPI app with Rs <price> prefilled.
        lines.append(f"Tap to pay Rs {price:,} and renew: {base}/pay?plan={plan.value}")
        lines.append(f"UPI id (if you prefer to type it): {upi}")
    elif upi:
        lines.append(f"Pay Rs {price:,} to renew.  UPI: {upi}")
    if team:
        lines.append(f"Or message us to renew: https://wa.me/{team}")
    return "\n".join(lines)
