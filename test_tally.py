import httpx
import asyncio

URL = "http://192.168.0.9:9000"

XML_TDL_BILLS = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>CustomBillsReport</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <REPORT NAME="CustomBillsReport">
            <FORMS>CustomBillsForm</FORMS>
          </REPORT>
          <FORM NAME="CustomBillsForm">
            <PARTS>CustomBillsPart</PARTS>
          </FORM>
          <PART NAME="CustomBillsPart">
            <LINES>CustomBillsLine</LINES>
            <REPEAT>CustomBillsLine : CustomBillsCollection</REPEAT>
            <SCROLLED>Vertical</SCROLLED>
          </PART>
          <LINE NAME="CustomBillsLine">
            <FIELDS>BillLedger, BillName, BillDate, BillAmount, BillDue</FIELDS>
          </LINE>
          <FIELD NAME="BillLedger">
            <SET>$PartyLedgerName</SET>
            <XMLTAG>"LedgerName"</XMLTAG>
          </FIELD>
          <FIELD NAME="BillName">
            <SET>$Name</SET>
            <XMLTAG>"InvoiceNumber"</XMLTAG>
          </FIELD>
          <FIELD NAME="BillDate">
            <SET>$BillDate</SET>
            <XMLTAG>"InvoiceDate"</XMLTAG>
          </FIELD>
          <FIELD NAME="BillAmount">
            <SET>$OpeningBalance</SET>
            <XMLTAG>"Amount"</XMLTAG>
          </FIELD>
          <FIELD NAME="BillDue">
            <SET>$BillCreditPeriod</SET>
            <XMLTAG>"DueDate"</XMLTAG>
          </FIELD>
          <COLLECTION NAME="CustomBillsCollection">
            <TYPE>Bills</TYPE>
            <!-- Pending bills only -->
            <BELONGSTO>Yes</BELONGSTO>
          </COLLECTION>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

async def fetch_tdl():
    print(f"Fetching TDL Bills Collection from {URL}...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(URL, data=XML_TDL_BILLS, headers={"Content-Type": "text/xml"})
            
            raw_bytes = resp.content
            if raw_bytes.startswith(b'<'):
                text = raw_bytes.decode('utf-8', errors='replace')
                with open("tally_bills_tdl.xml", "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"Saved {len(text)} bytes to tally_bills_tdl.xml")
                
                # Print a few lines from the middle to see the data structure
                lines = text.splitlines()
                start = 0
                for i, line in enumerate(lines):
                    if "<CustomBillsLine" in line or "<CustomBillsReport" in line:
                        start = max(0, i - 1)
                        break
                
                print("\nSample Data:")
                print("\n".join(lines[start:start+40]))
            else:
                print("Response didn't look like XML:", raw_bytes[:50])
            
    except Exception as e:
        print(f"CONNECTION FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_tdl())
