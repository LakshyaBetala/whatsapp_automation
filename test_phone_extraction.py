"""Unit tests for pulling WhatsApp numbers out of Tally ledger data."""
from tally_agent.tally_xml import extract_indian_mobile, parse_ledger_contacts


def test_plain_mobile():
    assert extract_indian_mobile("9876543210") == "919876543210"


def test_spaced_mobile():
    assert extract_indian_mobile("98765 43210") == "919876543210"


def test_plus91_dashed():
    assert extract_indian_mobile("+91-9876543210") == "919876543210"


def test_leading_zero():
    assert extract_indian_mobile("09876543210") == "919876543210"


def test_mobile_inside_address_line():
    assert extract_indian_mobile("Shop 12, MG Road, Ph: 9822012345") == "919822012345"


def test_pincode_is_not_a_phone():
    assert extract_indian_mobile("MG Road, Mumbai 400001") is None


def test_landline_with_std_rejected():
    # 022-12345678 — landline, not a valid WhatsApp mobile
    assert extract_indian_mobile("022-12345678") is None


def test_house_number_and_pincode_only():
    assert extract_indian_mobile("H.No 1234, Sector 5, Pune 411001") is None


def test_empty_and_none():
    assert extract_indian_mobile("") is None
    assert extract_indian_mobile(None) is None


def test_parse_ledger_contacts_prefers_mobile_field():
    xml = """<ENVELOPE><BODY><DATA><COLLECTION>
      <LEDGER NAME="RAMESH TRADERS">
        <LEDGERMOBILE>9876543210</LEDGERMOBILE>
        <LEDGERPHONE></LEDGERPHONE>
        <ADDRESS.LIST><ADDRESS>MG Road, Mumbai 400001</ADDRESS></ADDRESS.LIST>
      </LEDGER>
      <LEDGER NAME="SURESH AND CO">
        <LEDGERMOBILE></LEDGERMOBILE>
        <ADDRESS.LIST>
          <ADDRESS>Gala No 4, APMC Market</ADDRESS>
          <ADDRESS>Mob: 98220 11223</ADDRESS>
        </ADDRESS.LIST>
      </LEDGER>
      <LEDGER NAME="NO PHONE LEDGER">
        <ADDRESS.LIST><ADDRESS>Somewhere 400001</ADDRESS></ADDRESS.LIST>
      </LEDGER>
    </COLLECTION></DATA></BODY></ENVELOPE>"""
    contacts = parse_ledger_contacts(xml)
    assert contacts["RAMESH TRADERS"] == "919876543210"
    assert contacts["SURESH AND CO"] == "919822011223"
    assert "NO PHONE LEDGER" not in contacts


def test_parse_ledger_contacts_bad_xml():
    assert parse_ledger_contacts("<not-closed") == {}
