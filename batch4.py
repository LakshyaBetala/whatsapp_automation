import xml.etree.ElementTree as ET
from collections import defaultdict
import re

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

with open('raw_daybook_fullyear.xml', 'rb') as f:
    text = sanitize_xml(f.read())

root = ET.fromstring(text)

sales = []
receipts = []
other = []

for voucher in root.iter('VOUCHER'):
    vtype = voucher.attrib.get('VCHTYPE', voucher.findtext('VOUCHERTYPENAME', ''))
    party = voucher.findtext('PARTYLEDGERNAME', '')
    date = voucher.findtext('DATE', '')
    number = voucher.findtext('VOUCHERNUMBER', '')
    
    amount = 0
    # Search all child elements for AMOUNT to handle variable schema locations
    for entry in voucher.iter('AMOUNT'):
        amt_text = entry.text
        if amt_text:
            try:
                val = abs(float(amt_text.replace('-', '').strip()))
                if val > amount:
                    amount = val
            except:
                pass
    
    record = {
        'type': vtype,
        'party': party,
        'date': date,
        'number': number,
        'amount': amount
    }
    
    if 'Sales' in vtype:
        sales.append(record)
    elif 'Receipt' in vtype:
        receipts.append(record)
    else:
        other.append(record)

print(f"Sales vouchers: {len(sales)}")
print(f"Receipt vouchers: {len(receipts)}")
print(f"Other voucher types: {set(v['type'] for v in other)}")

all_v = sales + receipts
if all_v:
    print(f"Date range: {min(v['date'] for v in all_v)} to {max(v['date'] for v in all_v)}")

print(f"\nFirst 5 sales:")
for s in sales[:5]:
    print(s)
print(f"\nFirst 5 receipts:")    
for r in receipts[:5]:
    print(r)

# compute outstanding per party
outstanding = defaultdict(float)
for s in sales:
    outstanding[s['party']] += s['amount']
for r in receipts:
    outstanding[r['party']] -= r['amount']

print(f"\nTop 10 outstanding parties:")
sorted_outstanding = sorted(outstanding.items(), key=lambda x: x[1], reverse=True)
for party, amount in sorted_outstanding[:10]:
    if amount > 0:
        print(f"  {party}: Rs. {amount:,.2f}")
