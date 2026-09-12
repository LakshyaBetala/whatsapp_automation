"""The accounting-source contract: what ANY reader must produce for ASVA.

The backend is source-agnostic. The dashboard, bot, reminders, pay page and
promise tracking never mention Tally; they consume these shapes through
/tally/import, /tally/outstandings and /tally/sync (the paths keep their name
for compatibility). tally_agent/ is one implementation of this contract; a
BUSY reader is another. The desktop app, the interface and every feature stay
byte-for-byte the same - only the reader behind "Get latest from Tally" changes.

This file is the SPEC the BUSY adapter targets. It is deliberately plain
dataclasses with no framework so it can be read by anyone - including the
channel-partner developer BUSY routes integrations through.

Field meanings are the ones the backend already relies on (see
app/routers/tally.py), restated here so a second reader cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol


# ── What a reader hands ASVA ─────────────────────────────────────────────

@dataclass
class Party:
    """One customer (a debtor ledger / party master)."""
    name: str                          # exact ledger name in the accounting software
    guid: Optional[str] = None         # the software's OWN stable id; survives a rename.
                                       # Tally: GUID. BUSY: its master code / row id.
    whatsapp_number: Optional[str] = None   # 10-digit Indian mobile if the master has one
    credit_days: Optional[int] = None  # per-party credit period, if the software stores it
    group: Optional[str] = None        # the group/category the party sits under
    opening_balance: float = 0.0       # FY-start balance, POSITIVE = customer owes
    closing_balance: float = 0.0       # balance TODAY, POSITIVE = customer owes


@dataclass
class OpenBill:
    """One unpaid (or part-paid) bill, bill-by-bill."""
    party_name: str
    bill_ref: str                      # invoice / reference number
    amount: float                      # NET still outstanding on THIS bill
    bill_date: Optional[date] = None
    due_date: Optional[date] = None


@dataclass
class Voucher:
    """A sales invoice or a receipt, as booked in the software."""
    voucher_number: str
    voucher_type: str                  # "Sales" | "Receipt"
    party_name: str
    amount: float
    date: date
    pdf_base64: Optional[str] = None   # the software's own invoice PDF, if it can export one


@dataclass
class OutstandingSnapshot:
    """Everything /tally/outstandings needs in ONE read."""
    parties: list[Party]               # EVERY debtor, so new ones auto-create
    bills: list[OpenBill]              # bill-wise open items (may be empty for a party)
    ledger_balances: dict[str, float] = field(default_factory=dict)
                                       # party name -> closing balance (authoritative total)


# ── What a reader must be able to do ─────────────────────────────────────

class AccountingSource(Protocol):
    """Implemented once per accounting software. Tally: tally_agent. BUSY: busy_agent."""

    name: str                          # "tally" | "busy"

    def list_companies(self) -> list[str]:
        """Companies currently open/available. Empty = software not running."""

    def is_ready(self, company: str) -> tuple[bool, str]:
        """(ready, reason). Must be CHEAP and must not hurt the software.
        Tally taught us this the hard way: a heavy read while the company is
        still loading crashed it with a memory violation."""

    def read_outstanding(self, company: str) -> OutstandingSnapshot:
        """Debtors + bill-wise open items + closing balances. Runs every few
        minutes, so it must be safe to repeat and must not lock the software."""

    def read_vouchers(self, company: str, since: date, until: date) -> list[Voucher]:
        """Sales + receipts booked in a date window (day book / register)."""

    def post_receipt(self, company: str, party_name: str, amount: float,
                     on: date, deposit_ledger: str, narration: str) -> str:
        """WRITE a receipt the owner has CONFIRMED. Returns the new voucher id.
        Raise NotImplementedError if the software offers no safe write path;
        ASVA then keeps the receipt in its queue and tells the owner to book it.
        Never write directly into a database file - use the software's own
        import mechanism (Tally: XML import over HTTP; BUSY: XML import)."""


# ── What is known about BUSY, and what discover.py must still answer ─────
#
# KNOWN (BUSY's own FAQ + product docs, Sept 2026):
#   * No default third-party integration; its API is channel-partner only with
#     no public spec. So there is no Tally-style always-on local HTTP server.
#   * Company data lives in MS Access (.mdb / .bds, file-server edition) or in
#     MS SQL Server (client-server edition). Both are readable over ODBC from
#     Python with pyodbc, read-only, while BUSY is open (multi-user by design).
#   * BUSY can IMPORT vouchers from XML (the Tally2BUSY utility relies on it),
#     which is the candidate write path for post_receipt.
#
# UNKNOWN until busy_discovery.json comes back from a real install:
#   * the table + column names for party masters, groups, vouchers, bill-wise
#     references and receipts (the schema is not published)
#   * whether the company file is password-protected (then read via BUSY's own
#     SQL route instead of the raw file)
#   * the exact XML shape BUSY accepts for a receipt import, and whether it can
#     be triggered without a human clicking Import
#
# The read side (read_outstanding, read_vouchers) is expected to be
# straightforward once the schema is known. post_receipt is the open question,
# exactly as it was for Tally before build_receipt_import was proven live.
