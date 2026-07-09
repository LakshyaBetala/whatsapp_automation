"""Tests for bill-by-bill outstanding parsing (accurate amount + dates)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tally_agent"))

import tally_xml as tx


def test_flexible_date_formats():
    assert tx._parse_flexible_date("20210401") == "2021-04-01"
    assert tx._parse_flexible_date("1-Apr-21") == "2021-04-01"
    assert tx._parse_flexible_date("1-Apr-2021") == "2021-04-01"
    assert tx._parse_flexible_date("2021-04-01") == "2021-04-01"
    assert tx._parse_flexible_date("") is None
    assert tx._parse_flexible_date("garbage") is None


SAMPLE = """<ENVELOPE>
  <BILL><NAME>INV001</NAME><PARENT>PINEMA</PARENT><BILLDATE>20260406</BILLDATE>
    <CLOSINGBALANCE>-33364.00</CLOSINGBALANCE><BILLCREDITPERIOD>30 Days</BILLCREDITPERIOD></BILL>
  <BILL NAME="INV002"><PARENT>PINEMA</PARENT><BILLDATE>1-May-2026</BILLDATE>
    <CLOSINGBALANCE>-44100</CLOSINGBALANCE><BILLCREDITPERIOD></BILLCREDITPERIOD></BILL>
  <BILL><NAME>ADV</NAME><PARENT>PINEMA</PARENT><BILLDATE>20260401</BILLDATE>
    <CLOSINGBALANCE>5000</CLOSINGBALANCE></BILL>
  <BILL><NAME>Y</NAME><PARENT>SOME CREDITOR</PARENT><BILLDATE>20260401</BILLDATE>
    <CLOSINGBALANCE>-1000</CLOSINGBALANCE></BILL>
</ENVELOPE>"""


def test_parse_bills_debtor_filter_and_sign():
    bills = tx.parse_bills(SAMPLE, {"PINEMA"})
    refs = {b["bill_ref"]: b for b in bills}
    # advance (positive closing) dropped; creditor (not a debtor) dropped
    assert set(refs) == {"INV001", "INV002"}
    # closing balance sign-flipped: owed -> positive
    assert refs["INV001"]["amount"] == 33364.00
    assert refs["INV002"]["amount"] == 44100.00


def test_parse_bills_dates_and_due():
    bills = {b["bill_ref"]: b for b in tx.parse_bills(SAMPLE, {"PINEMA"})}
    # real bill date + credit period -> due date
    assert bills["INV001"]["bill_date"] == "2026-04-06"
    assert bills["INV001"]["due_date"] == "2026-05-06"   # +30 days
    # non-numeric date format parsed; no credit period -> due = bill date
    assert bills["INV002"]["bill_date"] == "2026-05-01"
    assert bills["INV002"]["due_date"] == "2026-05-01"


def test_parse_bills_no_filter_returns_all_owed():
    bills = tx.parse_bills(SAMPLE)   # no debtor filter
    refs = {b["bill_ref"] for b in bills}
    assert refs == {"INV001", "INV002", "Y"}   # only the advance (positive) is dropped
