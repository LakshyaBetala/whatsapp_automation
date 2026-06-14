import asyncio
import httpx
import xml.etree.ElementTree as ET
import re

URL = "http://192.168.0.9:9000"

QUERIES = [
    {
        "name": "raw_ledger_balances.xml",
        "payload": """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>List of Accounts</REPORTNAME>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>RISHAB TRADING COMPANY</SVCURRENTCOMPANY>
</STATICVARIABLES>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_outstanding_3.xml",
        "payload": """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>Outstanding Receivables</REPORTNAME>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>RISHAB TRADING COMPANY</SVCURRENTCOMPANY>
<SVFROMDATE>20260401</SVFROMDATE>
<SVTODATE>20260613</SVTODATE>
</STATICVARIABLES>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_all_ledgers.xml",
        "payload": """<ENVELOPE>
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
<COLLECTION NAME="LedgerCollection" ISMODIFY="No">
<TYPE>Ledger</TYPE>
<FETCHLIST>
<FETCH>Name</FETCH>
<FETCH>Parent</FETCH>
<FETCH>ClosingBalance</FETCH>
<FETCH>OpeningBalance</FETCH>
<FETCH>BillAllocations</FETCH>
</FETCHLIST>
</COLLECTION>
</TALLYMESSAGE>
</REQUESTDATA>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_company_full.xml",
        "payload": """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>Company Info</REPORTNAME>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
</STATICVARIABLES>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_billwise.xml",
        "payload": """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>Bill Outstanding</REPORTNAME>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>RISHAB TRADING COMPANY</SVCURRENTCOMPANY>
<SVFROMDATE>20260401</SVFROMDATE>
<SVTODATE>20260613</SVTODATE>
</STATICVARIABLES>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_stock.xml",
        "payload": """<ENVELOPE>
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
<COLLECTION NAME="StockCollection" ISMODIFY="No">
<TYPE>Stock Item</TYPE>
<FETCHLIST>
<FETCH>Name</FETCH>
<FETCH>ClosingBalance</FETCH>
<FETCH>OpeningBalance</FETCH>
<FETCH>Parent</FETCH>
<FETCH>BaseUnits</FETCH>
</FETCHLIST>
</COLLECTION>
</TALLYMESSAGE>
</REQUESTDATA>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    },
    {
        "name": "raw_pl.xml",
        "payload": """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>Profit and Loss</REPORTNAME>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>RISHAB TRADING COMPANY</SVCURRENTCOMPANY>
<SVFROMDATE>20260401</SVFROMDATE>
<SVTODATE>20260613</SVTODATE>
</STATICVARIABLES>
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""
    }
]

def sanitize_xml(raw_bytes: bytes) -> str:
    for encoding in ['utf-16', 'windows-1252', 'utf-8']:
        try:
            text = raw_bytes.decode(encoding)
            break
        except Exception:
            continue
    else:
        text = raw_bytes.decode('utf-8', errors='ignore')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'&#x[0-9A-Fa-f]+;', '', text)
    text = re.sub(r'&#\d+;', '', text)
    return text

async def run():
    print("=== STARTING PHASE 2 DATA DUMP ===")
    results = []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for q in QUERIES:
            print(f"Fetching {q['name']}...")
            try:
                resp = await client.post(URL, data=q['payload'], headers={"Content-Type": "text/xml"})
                
                with open(q['name'], "wb") as f:
                    f.write(resp.content)
                
                results.append({
                    "name": q['name'],
                    "size": len(resp.content),
                    "status": resp.status_code,
                    "error": None,
                    "raw_bytes": resp.content
                })
            except Exception as e:
                print(f"  -> Error: {e}")
                results.append({
                    "name": q['name'],
                    "size": 0,
                    "status": 0,
                    "error": str(e),
                    "raw_bytes": b""
                })

    print("\n=== RUNNING ANALYSIS ON PHASE 2 FILES ===")
    for r in results:
        print(f"\nFile: {r['name']}")
        print(f"  Size: {r['size']} bytes")
        print(f"  HTTP Status: {r['status']}")
        
        if r['error']:
            print(f"  Error: {r['error']}")
            continue
            
        if r['size'] == 0:
            print("  Warning: Empty response.")
            continue
            
        try:
            sanitized = sanitize_xml(r['raw_bytes'])
            print(f"  First 500 chars: {sanitized[:500]!r}")
            root = ET.fromstring(sanitized)
            print("  Parse clean: YES")
            
            # Count records
            count = 0
            if 'OBJECT' in sanitized:
                count = len(root.findall('.//OBJECT'))
                print(f"  Records (OBJECT) found: {count}")
            elif 'VOUCHER' in sanitized:
                count = len(root.findall('.//VOUCHER'))
                print(f"  Records (VOUCHER) found: {count}")
            elif 'LEDGER' in sanitized:
                count = len(root.findall('.//LEDGER'))
                print(f"  Records (LEDGER) found: {count}")
            else:
                # Count child elements of DSPACCINFO if it exists
                dsp = root.findall('.//DSPACCINFO')
                if dsp:
                    print(f"  Records (DSPACCINFO) found: {len(dsp)}")
                else:
                    print(f"  Records (Direct Children) found: {len(list(root))}")
                
        except Exception as e:
            print(f"  Parse clean: NO")
            print(f"  Parse Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
