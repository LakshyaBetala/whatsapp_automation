import xml.etree.ElementTree as ET
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

def sanitize_xml(raw_bytes: bytes) -> str:
    """Safely decodes and sanitizes Tally XML to remove invalid control characters."""
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

def parse_tally_date(date_str: str) -> Optional[str]:
    """Converts 'YYYYMMDD' to 'YYYY-MM-DD'."""
    if not date_str or len(date_str) != 8:
        return None
    try:
        dt = datetime.strptime(date_str, '%Y%m%d')
        return dt.strftime('%Y-%m-%d')
    except ValueError:
        return None

def extract_amount(element: ET.Element) -> float:
    """Extracts absolute float amount from anywhere within the element."""
    amount = 0.0
    for entry in element.iter('AMOUNT'):
        amt_text = entry.text
        if amt_text:
            try:
                val = abs(float(amt_text.replace('-', '').strip()))
                if val > amount:
                    amount = val
            except ValueError:
                pass
    return amount

def build_all_masters_query() -> str:
    """Query to fetch all masters to extract debtors."""
    return '''<ENVELOPE>
<HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
<BODY><EXPORTDATA><REQUESTDESC>
<REPORTNAME>List of Accounts</REPORTNAME>
<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
</REQUESTDESC></EXPORTDATA></BODY>
</ENVELOPE>'''

def build_daybook_query() -> str:
    """
    Query to fetch Day Book. 
    Note: Tally ignores SVFROMDATE/SVTODATE over HTTP without custom TDL.
    This will return the active period in the Tally UI.
    """
    return '''<ENVELOPE>
<HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
<BODY><EXPORTDATA><REQUESTDESC>
<REPORTNAME>Day Book</REPORTNAME>
<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
</REQUESTDESC></EXPORTDATA></BODY>
</ENVELOPE>'''

def parse_debtors(xml_text: str) -> List[Dict[str, Any]]:
    """Parses All Masters XML to extract Sundry Debtors and Opening Balances using full group hierarchy."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # 1. Build Group Hierarchy
    group_parents = {}
    for group in root.iter('GROUP'):
        name = group.get('NAME', group.findtext('NAME', ''))
        parent = group.findtext('PARENT', '')
        if name:
            group_parents[name] = parent

    def is_debtor(group_name: str) -> bool:
        if not group_name: 
            return False
        if 'sundry debtor' in group_name.lower(): 
            return True
        parent = group_parents.get(group_name)
        if parent:
            return is_debtor(parent)
        return False

    # 2. Extract Ledgers
    debtors = []
    for ledger in root.iter('LEDGER'):
        parent = ledger.findtext('PARENT', '')
        if is_debtor(parent):
            name = ledger.get('NAME', ledger.findtext('NAME', ''))
            opening_str = ledger.findtext('OPENINGBALANCE', '0')
            
            try:
                opening_balance = float(opening_str.strip())
            except ValueError:
                opening_balance = 0.0
                
            debtors.append({
                'name': name,
                'opening_balance': opening_balance,
                'parent': parent
            })
    return debtors

def parse_daybook(xml_text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parses Day Book XML to extract Sales and Receipt vouchers."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {'sales': [], 'receipts': []}

    sales = []
    receipts = []

    for voucher in root.iter('VOUCHER'):
        vtype = voucher.attrib.get('VCHTYPE', voucher.findtext('VOUCHERTYPENAME', ''))
        party = voucher.findtext('PARTYLEDGERNAME', '')
        raw_date = voucher.findtext('DATE', '')
        number = voucher.findtext('VOUCHERNUMBER', '')
        
        if not party or not raw_date:
            continue
            
        formatted_date = parse_tally_date(raw_date)
        if not formatted_date:
            continue
            
        amount = extract_amount(voucher)
        
        record = {
            'type': vtype,
            'party': party,
            'date': formatted_date,
            'number': number,
            'amount': amount
        }
        
        if 'Sales' in vtype:
            sales.append(record)
        elif 'Receipt' in vtype:
            receipts.append(record)

    return {
        'sales': sales,
        'receipts': receipts
    }
