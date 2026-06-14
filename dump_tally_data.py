import asyncio
import httpx
from datetime import datetime, timedelta
import re
import xml.etree.ElementTree as ET

HOST = "192.168.0.9"
PORT = 9000
URL = f"http://{HOST}:{PORT}"

def build_envelope(report_name, sv_from=None, sv_to=None, extra_sv=""):
    sv = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    if sv_from: sv += f"<SVFROMDATE>{sv_from}</SVFROMDATE>"
    if sv_to: sv += f"<SVTODATE>{sv_to}</SVTODATE>"
    sv += extra_sv
    return f"""<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC><REPORTNAME>{report_name}</REPORTNAME><STATICVARIABLES>{sv}</STATICVARIABLES></REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>"""

def build_collection(name, type_val, filters=None, fetch_list=None):
    filters_xml = f"<FILTER>{filters}</FILTER>" if filters else ""
    fetch_xml = "<FETCHLIST>" + "".join(f"<FETCH>{f}</FETCH>" for f in fetch_list) + "</FETCHLIST>" if fetch_list else ""
    return f"""<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><REQUESTDATA><TALLYMESSAGE><COLLECTION JSFETCH="Yes" NAME="{name}"><TYPE>{type_val}</TYPE>{filters_xml}{fetch_xml}</COLLECTION></TALLYMESSAGE></REQUESTDATA></REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>"""

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

today = datetime.now()
today_str = today.strftime("%Y%m%d")
thirty_days_ago = (today - timedelta(days=30)).strftime("%Y%m%d")

QUERIES = [
    {
        "name": "raw_daybook_today.xml",
        "payload": build_envelope("Day Book", today_str, today_str)
    },
    {
        "name": "raw_daybook_30days.xml",
        "payload": build_envelope("Day Book", thirty_days_ago, today_str)
    },
    {
        "name": "raw_receipts_today.xml",
        "payload": build_envelope("Receipt Register", today_str, today_str)
    },
    {
        "name": "raw_receipts_30days.xml",
        "payload": build_envelope("Receipt Register", thirty_days_ago, today_str)
    },
    {
        "name": "raw_debtors.xml",
        "payload": build_collection("DebtorsCol", "Ledger", "IsDebtorLedger", ["Name", "ClosingBalance", "OpeningBalance", "Parent"])
    },
    {
        "name": "raw_outstanding_1.xml",
        "payload": build_envelope("GroupOutstandings", thirty_days_ago, today_str, "<GROUPNAME>Sundry Debtors</GROUPNAME>")
    },
    {
        "name": "raw_outstanding_2.xml",
        "payload": build_envelope("Bills Outstanding", thirty_days_ago, today_str, "<GROUPNAME>Sundry Debtors</GROUPNAME>")
    },
    {
        "name": "raw_company.xml",
        "payload": build_collection("CompanyCol", "Company", None, ["Name", "BooksFrom", "StartingFrom"])
    }
]

async def run():
    print("=== STARTING TALLY DATA DUMP ===")
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
                print(f"  -> Saved {len(resp.content)} bytes.")
            except Exception as e:
                print(f"  -> Error: {e}")
                results.append({
                    "name": q['name'],
                    "size": 0,
                    "status": 0,
                    "error": str(e),
                    "raw_bytes": b""
                })

    print("\n=== RUNNING ANALYSIS ON DUMPED FILES ===")
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
            print("  Parse clean? Testing ElementTree...")
            root = ET.fromstring(sanitized)
            print("  Parse clean: YES")
            
            # Additional analysis based on file type
            if 'daybook' in r['name'] or 'receipts' in r['name']:
                vouchers = root.findall('.//VOUCHER')
                print(f"  Voucher records found: {len(vouchers)}")
                if vouchers:
                    print(f"  Field names in first VOUCHER: {[child.tag for child in vouchers[0]]}")
                    print(f"  Date format found: {vouchers[0].findtext('DATE')}")
                    
            elif 'debtor' in r['name']:
                objs = root.findall('.//OBJECT')
                print(f"  Debtor ledgers found: {len(objs)}")
                
        except Exception as e:
            print(f"  Parse clean: NO")
            print(f"  Parse Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
