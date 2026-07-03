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
    if not date_str or len(date_str) != 8:
        return None
    try:
        dt = datetime.strptime(date_str, '%Y%m%d')
        return dt.strftime('%Y-%m-%d')
    except ValueError:
        return None

def extract_amount(element: ET.Element) -> float:
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

def build_receivables_query() -> str:
    """Queries the physical tally_saas.tdl report for Debtors."""
    return '''<ENVELOPE>
    <HEADER>
      <VERSION>1</VERSION>
      <TALLYREQUEST>EXPORT</TALLYREQUEST>
      <TYPE>DATA</TYPE>
      <ID>SAAS Debtors</ID>
    </HEADER>
    <BODY>
      <DESC>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        </STATICVARIABLES>
      </DESC>
    </BODY>
  </ENVELOPE>'''

def build_payables_query() -> str:
    return '''<ENVELOPE>
    <HEADER>
      <VERSION>1</VERSION>
      <TALLYREQUEST>EXPORT</TALLYREQUEST>
      <TYPE>DATA</TYPE>
      <ID>SAAS Creditors</ID>
    </HEADER>
    <BODY>
      <DESC>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        </STATICVARIABLES>
      </DESC>
    </BODY>
  </ENVELOPE>'''

def build_daybook_query(from_date="20230401", to_date="20260331") -> str:
    """Queries the physical tally_saas.tdl report for Daybook/Vouchers."""
    return f'''<ENVELOPE>
    <HEADER>
      <VERSION>1</VERSION>
      <TALLYREQUEST>EXPORT</TALLYREQUEST>
      <TYPE>DATA</TYPE>
      <ID>SAAS Daybook</ID>
    </HEADER>
    <BODY>
      <DESC>
        <STATICVARIABLES>
          <SVFROMDATE>{from_date}</SVFROMDATE>
          <SVTODATE>{to_date}</SVTODATE>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        </STATICVARIABLES>
      </DESC>
    </BODY>
  </ENVELOPE>'''

def parse_debtors(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    debtors = []
    
    # SAAS Debtors Line is the repetitive node in our TDL
    for line in root.iter('SAASDEBTORSLINE'):
        name = line.findtext('PARTYNAME', '')
        opening_str = line.findtext('CLOSINGBALANCE', '0')
        try:
            closing_balance = float(opening_str.replace('-', '').strip())
        except ValueError:
            closing_balance = 0.0
            
        bills = []
        for bill_alloc in line.iter('SAASBILLALLOCLINE'):
            bill_name = bill_alloc.findtext('BILLNAME', '')
            bill_date = bill_alloc.findtext('BILLDATE', '')
            bill_amount_str = bill_alloc.findtext('BILLAMOUNT', '0')
            try:
                bill_amount = abs(float(bill_amount_str.replace('-', '').strip()))
            except ValueError:
                bill_amount = 0.0
                
            if bill_name and bill_amount > 0:
                bills.append({
                    'bill_name': bill_name,
                    'bill_date': bill_date,
                    'amount': bill_amount
                })

        debtors.append({
            'name': name,
            'closing_balance': closing_balance,
            'bills': bills
        })
        
    return debtors

def parse_daybook(xml_text: str) -> Dict[str, List[Dict[str, Any]]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {'sales': [], 'receipts': []}

    sales = []
    receipts = []

    for line in root.iter('SAASDAYBOOKLINE'):
        vtype = line.findtext('VOUCHERTYPENAME', '')
        party = line.findtext('PARTYLEDGERNAME', '')
        raw_date = line.findtext('DATE', '')
        number = line.findtext('VOUCHERNUMBER', '')
        amount_str = line.findtext('AMOUNT', '0')
        
        if not party or not raw_date:
            continue
            
        formatted_date = parse_tally_date(raw_date)
        if not formatted_date:
            continue
            
        try:
            amount = abs(float(amount_str.replace('-', '').strip()))
        except ValueError:
            amount = 0.0
        
        # Extract deep inventory details
        items = []
        for inv_line in line.iter('SAASVCHINVLINE'):
            items.append({
                'item_name': inv_line.findtext('STOCKITEMNAME', ''),
                'qty': inv_line.findtext('BILLEDQTY', ''),
                'rate': inv_line.findtext('RATE', ''),
                'amount': inv_line.findtext('ITEMAMOUNT', '0')
            })

        record = {
            'type': vtype,
            'party': party,
            'date': formatted_date,
            'number': number,
            'amount': amount,
            'items': items
        }
        
        if 'Sales' in vtype:
            sales.append(record)
        elif 'Receipt' in vtype:
            receipts.append(record)

    return {
        'sales': sales,
        'receipts': receipts
    }
