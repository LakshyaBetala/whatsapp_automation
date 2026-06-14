import xml.etree.ElementTree as ET
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

def parse_debtors(filepath):
    print(f"Reading and sanitizing {filepath}...")
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    
    text = sanitize_xml(raw_bytes)
    root = ET.fromstring(text)
    
    debtors = []
    for ledger in root.iter('LEDGER'):
        parent = ledger.findtext('PARENT', '')
        if 'Sundry Debtor' in parent or 'sundry debtor' in parent.lower():
            name = ledger.get('NAME', ledger.findtext('NAME', ''))
            closing = ledger.findtext('CLOSINGBALANCE', '0')
            opening = ledger.findtext('OPENINGBALANCE', '0')
            debtors.append({
                'name': name,
                'closing_balance': closing,
                'opening_balance': opening,
                'parent': parent
            })
    print(f"Total debtors found: {len(debtors)}")
    for d in debtors[:10]:
        print(d)
    
    # Save first debtor name for BATCH 2
    if debtors:
        with open('first_debtor.txt', 'w', encoding='utf-8') as f:
            f.write(debtors[0]['name'])
            
    return debtors

if __name__ == "__main__":
    parse_debtors('raw_ledger_balances.xml')
