"""Enums and Pydantic schemas shared across the app.

The enum *values* mirror the Postgres enum types in
migrations/001_initial_schema.sql — keep them in sync.
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


# Plan limits — mirror plan_max_* SQL functions.
PLAN_LIMITS: dict[Plan, dict[str, int]] = {
    Plan.starter: {"clients": 50, "messages": 250},
    Plan.growth: {"clients": 150, "messages": 750},
    Plan.pro: {"clients": 250, "messages": 1250},
    Plan.max: {"clients": 500, "messages": 2500},
}

# Reminder cadence (days after due date). Credit period is handled by due_date.
REMINDER_DAYS: tuple[int, ...] = (7, 15, 30, 45, 60)


# --- Payloads from the Tally agent ------------------------------------
class TallyVoucher(BaseModel):
    """A sales voucher the agent detected in Tally."""
    voucher_number: str
    ledger_name: str                 # debtor — matched to clients.tally_ledger_name
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
