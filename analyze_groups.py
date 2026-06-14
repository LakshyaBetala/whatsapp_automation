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

with open('raw_ledger_balances.xml', 'rb') as f:
    text = sanitize_xml(f.read())
root = ET.fromstring(text)

# Map group to its parent
group_parents = {}
for group in root.iter('GROUP'):
    name = group.get('NAME', group.findtext('NAME', ''))
    parent = group.findtext('PARENT', '')
    if name:
        group_parents[name] = parent

def is_debtor(group_name):
    if not group_name: return False
    if 'sundry debtor' in group_name.lower(): return True
    parent = group_parents.get(group_name)
    if parent:
        return is_debtor(parent)
    return False

# Now find all ledgers that roll up to a debtor group
debtors = []
for ledger in root.iter('LEDGER'):
    name = ledger.get('NAME', ledger.findtext('NAME', ''))
    parent = ledger.findtext('PARENT', '')
    if is_debtor(parent):
        debtors.append({'name': name, 'parent': parent})

print(f"Groups total: {len(group_parents)}")
print(f"Ledgers total: {len(list(root.iter('LEDGER')))}")
print(f"Debtors found using tree: {len(debtors)}")

# Also let's print some group names to see what's up
print("\nSample Groups:")
for g in list(group_parents.keys())[:10]:
    print(f" - {g} (Parent: {group_parents[g]})")

print("\nSample Debtors:")
for d in debtors[:10]:
    print(f" - {d['name']} (Direct Parent: {d['parent']})")
