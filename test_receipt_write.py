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
