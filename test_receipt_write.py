"""Tally receipt WRITE builder (tally_xml.build_receipt_import + import_succeeded).

Verified against RISHAB's real receipts: party credited (ISDEEMEDPOSITIVE=No,
positive) with Agst Ref bill allocations; Cash/Bank debited (ISDEEMEDPOSITIVE=Yes,
negative). These tests lock that exact shape and the success/erro parsing, so we
never post a malformed voucher into a shop's books.
"""
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from tally_agent import tally_xml as tx


def _voucher(xml):
    root = ET.fromstring(xml)
    v = root.find(".//VOUCHER")
    assert v is not None
    return v


def _entries(v):
    return v.findall("ALLLEDGERENTRIES.LIST")


def test_receipt_is_wellformed_and_balanced():
    xml = tx.build_receipt_import(
        "RISHAB TRADING COMPANY", "Ramesh Traders", "CASH", "20260730",
        [("2526RTC0363", 5000)])
    v = _voucher(xml)
    assert v.get("VCHTYPE") == "Receipt" and v.get("ACTION") == "Create"
    assert v.findtext("DATE") == "20260730"
    assert v.findtext("PARTYLEDGERNAME") == "Ramesh Traders"
    party, deposit = _entries(v)
    # party = credit, positive
    assert party.findtext("LEDGERNAME") == "Ramesh Traders"
    assert party.findtext("ISDEEMEDPOSITIVE") == "No"
    assert party.findtext("AMOUNT") == "5000.00"
    # deposit = debit, negative, equal and opposite
    assert deposit.findtext("LEDGERNAME") == "CASH"
    assert deposit.findtext("ISDEEMEDPOSITIVE") == "Yes"
    assert deposit.findtext("AMOUNT") == "-5000.00"


def test_bill_allocations_are_agst_ref_fifo():
    xml = tx.build_receipt_import(
        "Co", "Party", "CASH", "20260730",
        [("BILL-A", 550), ("BILL-B", 1450)])
    party = _entries(_voucher(xml))[0]
    bills = party.findall("BILLALLOCATIONS.LIST")
    assert [b.findtext("NAME") for b in bills] == ["BILL-A", "BILL-B"]   # FIFO order
    assert all(b.findtext("BILLTYPE") == "Agst Ref" for b in bills)
    assert [b.findtext("AMOUNT") for b in bills] == ["550.00", "1450.00"]
    assert party.findtext("AMOUNT") == "2000.00"                          # total


def test_drops_nonpositive_allocations():
    xml = tx.build_receipt_import(
        "Co", "Party", "CASH", "20260730",
        [("A", 1000), ("B", 0), ("C", -50), ("D", 500)])
    bills = _entries(_voucher(xml))[0].findall("BILLALLOCATIONS.LIST")
    assert [b.findtext("NAME") for b in bills] == ["A", "D"]
    assert _entries(_voucher(xml))[0].findtext("AMOUNT") == "1500.00"


def test_raises_when_no_positive_allocation():
    with pytest.raises(ValueError):
        tx.build_receipt_import("Co", "Party", "CASH", "20260730", [("A", 0)])
    with pytest.raises(ValueError):
        tx.build_receipt_import("Co", "Party", "CASH", "20260730", [])


def test_escapes_special_characters_in_names():
    xml = tx.build_receipt_import(
        "Co", "Suresh & Sons <KPM>", "CASH", "20260730", [("R&D-1", 100)])
    # must still parse (the & and <> are escaped, not raw)
    v = _voucher(xml)
    assert v.findtext("PARTYLEDGERNAME") == "Suresh & Sons <KPM>"
    assert _entries(v)[0].findall("BILLALLOCATIONS.LIST")[0].findtext("NAME") == "R&D-1"


def test_decimal_amounts_are_two_places():
    xml = tx.build_receipt_import("Co", "P", "CASH", "20260730",
                                  [("A", Decimal("1234.5"))])
    assert _entries(_voucher(xml))[0].findtext("AMOUNT") == "1234.50"


def test_company_scoping_present():
    xml = tx.build_receipt_import("RISHAB TRADING COMPANY", "P", "CASH", "20260730",
                                  [("A", 100)])
    assert "<SVCURRENTCOMPANY>RISHAB TRADING COMPANY</SVCURRENTCOMPANY>" in xml
    assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in xml


# ── import_succeeded ─────────────────────────────────────────────────────────
def test_import_success_detection():
    ok = "<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></RESPONSE>"
    assert tx.import_succeeded(ok) is True


def test_import_failure_detection():
    err = "<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><EXCEPTIONS>0</EXCEPTIONS><LINEERROR>Ledger not found</LINEERROR></RESPONSE>"
    assert tx.import_succeeded(err) is False
    # created but with an exception -> not a clean success
    mixed = "<RESPONSE><CREATED>1</CREATED><EXCEPTIONS>1</EXCEPTIONS></RESPONSE>"
    assert tx.import_succeeded(mixed) is False
    # nothing created
    assert tx.import_succeeded("<RESPONSE><CREATED>0</CREATED></RESPONSE>") is False


# ── deposit-account discovery (per-shop dropdown) ────────────────────────────
def test_parse_cash_bank_ledgers():
    xml = ('<ENVELOPE><COLLECTION>'
           '<LEDGER NAME="CASH"><PARENT>Cash-in-Hand</PARENT></LEDGER>'
           '<LEDGER NAME="HDFC BANK"><PARENT>Bank Accounts</PARENT></LEDGER>'
           '<LEDGER NAME="Kotak"><PARENT>Bank Accounts</PARENT></LEDGER>'
           '<LEDGER NAME="Ramesh Traders"><PARENT>Sundry Debtors</PARENT></LEDGER>'
           '</COLLECTION></ENVELOPE>')
    out = tx.parse_cash_bank_ledgers(xml)
    assert out == ["CASH", "HDFC BANK", "Kotak"]     # cash first, banks sorted, debtor excluded


def test_parse_cash_bank_handles_junk():
    assert tx.parse_cash_bank_ledgers("not xml") == []
    assert tx.parse_cash_bank_ledgers("<ENVELOPE></ENVELOPE>") == []


# ── party open-bills parser (drives FIFO allocation) ─────────────────────────
def test_parse_party_open_bills_owed_only_oldest_first():
    xml = ('<ENVELOPE><COLLECTION>'
           '<BILL NAME="B3"><BILLDATE>20260309</BILLDATE><CLOSINGBALANCE>-3000.00</CLOSINGBALANCE><PARENT>RAMESH KPM</PARENT></BILL>'
           '<BILL NAME="B1"><BILLDATE>20251021</BILLDATE><CLOSINGBALANCE>-2185.00</CLOSINGBALANCE><PARENT>RAMESH KPM</PARENT></BILL>'
           '<BILL NAME="B2"><BILLDATE>20251128</BILLDATE><CLOSINGBALANCE>500.00</CLOSINGBALANCE><PARENT>RAMESH KPM</PARENT></BILL>'
           '<BILL NAME="X1"><BILLDATE>20260101</BILLDATE><CLOSINGBALANCE>-999.00</CLOSINGBALANCE><PARENT>OTHER PARTY</PARENT></BILL>'
           '</COLLECTION></ENVELOPE>')
    out = tx.parse_party_open_bills(xml, "RAMESH KPM")
    # only owed (negative) bills of THIS party, oldest first, sign flipped
    assert [b["ref"] for b in out] == ["B1", "B3"]
    assert out[0] == {"ref": "B1", "date": "2025-10-21", "outstanding": 2185.0}
    assert out[1]["outstanding"] == 3000.0


def test_parse_party_open_bills_handles_junk():
    assert tx.parse_party_open_bills("nope", "P") == []
    assert tx.parse_party_open_bills("<ENVELOPE></ENVELOPE>", "P") == []


# ── FIFO allocation in the agent (mirrors the backend) ───────────────────────
def test_agent_allocate_fifo_spills_oldest_first():
    bills = [{"ref": "A", "outstanding": 550}, {"ref": "B", "outstanding": 3000}]
    allocs, on_acct = tx.allocate_fifo(bills, 2000)
    assert allocs == [("A", Decimal("550.00")), ("B", Decimal("1450.00"))]
    assert on_acct == Decimal("0.00")


def test_agent_allocate_fifo_overpayment_leaves_advance():
    bills = [{"ref": "A", "outstanding": 1000}]
    allocs, on_acct = tx.allocate_fifo(bills, 1500)
    assert allocs == [("A", Decimal("1000.00"))]
    assert on_acct == Decimal("500.00")


def test_agent_allocate_fifo_no_bills():
    allocs, on_acct = tx.allocate_fifo([], 800)
    assert allocs == [] and on_acct == Decimal("800.00")


# ── on-account (advance) receipt shape ───────────────────────────────────────
def test_receipt_with_advance_totals_full_amount():
    xml = tx.build_receipt_import("Co", "Party", "CASH", "20260730",
                                  [("A", 1000)], on_account="500")
    v = _voucher(xml)
    party, deposit = _entries(v)
    bills = party.findall("BILLALLOCATIONS.LIST")
    assert [b.findtext("BILLTYPE") for b in bills] == ["Agst Ref", "On Acc"]
    assert [b.findtext("AMOUNT") for b in bills] == ["1000.00", "500.00"]
    assert party.findtext("AMOUNT") == "1500.00"          # receipt = money received
    assert deposit.findtext("AMOUNT") == "-1500.00"       # deposit debit matches


def test_pure_advance_receipt_when_no_open_bills():
    xml = tx.build_receipt_import("Co", "Party", "CASH", "20260730", [], on_account="700")
    party = _entries(_voucher(xml))[0]
    bills = party.findall("BILLALLOCATIONS.LIST")
    assert len(bills) == 1 and bills[0].findtext("BILLTYPE") == "On Acc"
    assert party.findtext("AMOUNT") == "700.00"


# ── voucher id (for revert) ──────────────────────────────────────────────────
def test_parse_import_voucher_id():
    assert tx.parse_import_voucher_id("<RESPONSE><LASTVCHID>4567</LASTVCHID></RESPONSE>") == "4567"
    assert tx.parse_import_voucher_id("<RESPONSE><LASTVCHID>0</LASTVCHID></RESPONSE>") is None
    assert tx.parse_import_voucher_id("<RESPONSE></RESPONSE>") is None
