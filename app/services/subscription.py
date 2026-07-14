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
    """The 'how to pay' block appended to a renewal notice. Empty when no UPI is
    configured (settings.operator_upi_id), so notices degrade gracefully to a
    plain 'please renew'. Direct UPI: the owner pays, you confirm + click Renew."""
    upi = (settings.operator_upi_id or "").strip()
    if not upi:
        return ""
    plan, price = _plan_price(plan_value)
    label = PLAN_LABELS.get(plan, plan.value.title())
    name = settings.operator_upi_name or "ASVA"
    note = f"ASVA {label} renewal"
    link = f"upi://pay?pa={upi}&pn={quote(name)}&am={price}&cu=INR&tn={quote(note)}"
    return (f"Pay Rs {price:,} to renew:\n"
            f"UPI: {upi}\n"
            f"{link}")
