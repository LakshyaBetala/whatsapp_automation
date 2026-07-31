"""Enums and Pydantic schemas shared across the app.

The enum *values* mirror the Postgres enum types in
migrations/001_initial_schema.sql - keep them in sync.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums (mirror DB) -------------------------------------------------
class Plan(str, Enum):
    starter = "starter"
    growth = "growth"
    pro = "pro"
    max = "max"


class Lang(str, Enum):
    hi = "hi"
    gu = "gu"
    mr = "mr"


class BillStatus(str, Enum):
    pending = "pending"
    partial = "partial"
    paid = "paid"
    overdue = "overdue"


class MessageType(str, Enum):
    invoice = "invoice"
    reminder = "reminder"
    payment_confirmation = "payment_confirmation"
    eod_digest = "eod_digest"
    post_payment_pitch = "post_payment_pitch"
    low_stock = "low_stock"
    monthly_pnl = "monthly_pnl"
    welcome = "welcome"
    owner_alert = "owner_alert"
    bot_reply = "bot_reply"


class SyncType(str, Enum):
    poll = "poll"
    eod_force = "eod_force"
    import_ = "import"
    inventory = "inventory"
    pnl = "pnl"


# Plan limits + monthly price (INR). The message limit is enforced atomically
# in Postgres via increment_usage_if_allowed(p_limit=...), where p_limit is
# read from here - so these numbers ARE the live cap. Client caps are set high
# enough not to block Tally imports.
# Plans are metered by ACTIVE DEBTORS - parties with an open bill + a WhatsApp
# number that ASVA actually reminds. That is what the owner understands ("kitne
# khaate baaki hain") AND what drives cost (messages ~= debtors x touches), so
# revenue and cost scale together and margin stays put at any shop size.
#
# Priced at ~Rs2.3 / active-debtor / month. On paid Meta (~Rs0.15/utility msg,
# ~5-6 touches/debtor) this holds ~55% margin worst-case and ~70%+ typical; on
# OpenWA today the marginal cost is ~0 (near-100% margin).
#
# "messages" is a SILENT anti-abuse ceiling (debtors x 8), set well above real
# use so it never throttles reminders inside a tier. "clients" is uncapped in
# practice so a full Tally import is never blocked.
# "bot" = access to the ASVA owner assistant (LIST/BILL/photo/digest on WhatsApp).
# Basic does NOT include it; everything above does.
#   Basic   Rs699   -> 300 active debtors,  NO bot assistant
#   Growth  Rs1099  -> 500 active debtors,  bot assistant
#   Pro     Rs1999  -> 1000 active debtors, bot assistant
#   Custom  -> larger shops, bot assistant, priced on request (price 0 = "contact us")
PLAN_LIMITS: dict[Plan, dict[str, int]] = {
    Plan.starter: {"debtors": 300, "messages": 2400, "clients": 1000000, "price": 699, "bot": 0},
    Plan.growth: {"debtors": 500, "messages": 4000, "clients": 1000000, "price": 1099, "bot": 1},
    Plan.pro: {"debtors": 1000, "messages": 8000, "clients": 1000000, "price": 1999, "bot": 1},
    Plan.max: {"debtors": 5000, "messages": 40000, "clients": 1000000, "price": 0, "bot": 1},
}

# Owner-facing plan labels (the enum values stay stable in the DB).
PLAN_LABELS: dict[Plan, str] = {
    Plan.starter: "Basic",
    Plan.growth: "Growth",
    Plan.pro: "Pro",
    Plan.max: "Custom",
}


def plan_has_bot(plan_value) -> bool:
    """Does this plan include the ASVA owner assistant (bot)? Basic does not."""
    try:
        p = plan_value if isinstance(plan_value, Plan) else Plan(plan_value or "starter")
    except ValueError:
        p = Plan.starter
    return bool(PLAN_LIMITS[p].get("bot", 0))

# Tier order, cheapest first - used to recommend the right plan for a shop.
PLAN_ORDER: tuple[Plan, ...] = (Plan.starter, Plan.growth, Plan.pro, Plan.max)


def recommend_plan(active_debtors: int) -> Plan:
    """Smallest plan whose debtor cap covers this shop's active debtors."""
    for p in PLAN_ORDER:
        if active_debtors <= PLAN_LIMITS[p]["debtors"]:
            return p
    return Plan.max

# Reminder cadence (days after due date). Credit period is handled by due_date.
REMINDER_DAYS: tuple[int, ...] = (7, 15, 30, 45, 60)


# --- Payloads from the Tally agent ------------------------------------
class TallyVoucher(BaseModel):
    """A sales voucher the agent detected in Tally."""
    voucher_number: str
    ledger_name: str                 # debtor - matched to clients.tally_ledger_name
    amount: Decimal
    date: date
    invoice_number: Optional[str] = None


class TallyReceipt(BaseModel):
    """A payment receipt the agent detected in Tally."""
    ledger_name: str
    amount: Decimal
    date: date
    voucher_number: Optional[str] = None


class TallyOutstandingRow(BaseModel):
    """One bill-wise outstanding row from the one-click import."""
    ledger_name: str
    invoice_number: Optional[str] = None
    amount: Decimal
    invoice_date: date
    days_overdue: int = 0


class TallySyncPayload(BaseModel):
    """What the Windows agent POSTs to /tally/sync each poll."""
    business_id: str
    sync_type: SyncType = SyncType.poll
    vouchers: list[TallyVoucher] = Field(default_factory=list)
    receipts: list[TallyReceipt] = Field(default_factory=list)
    outstanding: list[TallyOutstandingRow] = Field(default_factory=list)


class TallySyncResult(BaseModel):
    bills_created: int = 0
    payments_applied: int = 0
    clients_created: int = 0
    errors: list[str] = Field(default_factory=list)


# --- Inbound WhatsApp (AiSensy webhook) -------------------------------
class InboundMessage(BaseModel):
    from_number: str
    text: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
