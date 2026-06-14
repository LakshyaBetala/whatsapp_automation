import asyncio

from tally_agent.tally_xml import (
    fetch_sales_vouchers,
    fetch_receipts,
    fetch_outstandings,
    sanitize_xml,
    build_envelope
)
import httpx

HOST = "192.168.0.9"
PORT = 9000

async def run_tests():
    print("=== TEST 2 & 3: fetch_sales_vouchers and fetch_receipts ===")
    sales, raw_xml_s = await fetch_sales_vouchers(HOST, PORT, "20240401", "20261231")
    receipts, raw_xml_r = await fetch_receipts(HOST, PORT, "20240401", "20261231")
    
    print(f"Sales Vouchers: {len(sales)}")
    if len(sales) > 0:
        for s in sales[:3]: print(f"  {s}")
    else:
        with open("test_sales_raw.xml", "w", encoding="utf-8") as f:
            f.write(raw_xml_s)
        print("0 sales fetched. Saved raw XML to test_sales_raw.xml")

    print(f"Receipts: {len(receipts)}")
    if len(receipts) > 0:
        for r in receipts[:3]: print(f"  {r}")
            
    print("\n=== TEST 4: fetch_outstandings (ODBC vs Ledger Vouchers) ===")
    
    # Test ODBC Collection
    xml_odbc = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        </STATICVARIABLES>
        <REQUESTDATA>
          <TALLYMESSAGE>
            <COLLECTION JSFETCH="Yes" NAME="MyDebtors">
              <TYPE>Ledger</TYPE>
              <FILTER>IsDebtorLedger</FILTER>
              <FETCHLIST>
                <FETCH>Name</FETCH>
                <FETCH>ClosingBalance</FETCH>
                <FETCH>OpeningBalance</FETCH>
                <FETCH>Parent</FETCH>
              </FETCHLIST>
            </COLLECTION>
          </TALLYMESSAGE>
        </REQUESTDATA>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

    # Test Ledger Vouchers
    xml_lv = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Ledger Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          <LEDGERNAME>Sundry Debtors</LEDGERNAME>
          <SVFROMDATE>20240401</SVFROMDATE>
          <SVTODATE>20261231</SVTODATE>
        </STATICVARIABLES>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp1 = await client.post(f"http://{HOST}:{PORT}", data=xml_odbc, headers={"Content-Type": "text/xml"})
        with open("test_odbc_raw.xml", "w", encoding="utf-8") as f:
            f.write(sanitize_xml(resp1.content))
        print(f"Saved ODBC raw response to test_odbc_raw.xml ({len(resp1.content)} bytes)")
        
        resp2 = await client.post(f"http://{HOST}:{PORT}", data=xml_lv, headers={"Content-Type": "text/xml"})
        with open("test_lv_raw.xml", "w", encoding="utf-8") as f:
            f.write(sanitize_xml(resp2.content))
        print(f"Saved Ledger Vouchers raw response to test_lv_raw.xml ({len(resp2.content)} bytes)")

if __name__ == "__main__":
    asyncio.run(run_tests())
