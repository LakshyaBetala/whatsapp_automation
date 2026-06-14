import os
import sys

# Add current dir to path to import tally_xml
sys.path.append(os.path.join(os.path.dirname(__file__), 'tally_agent'))

import tally_xml

def test_debtors():
    print("--- Testing Debtors Parser ---")
    filepath = 'raw_ledger_balances.xml'
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return
        
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    
    sanitized = tally_xml.sanitize_xml(raw_bytes)
    debtors = tally_xml.parse_debtors(sanitized)
    
    print(f"Found {len(debtors)} debtors.")
    for d in debtors:
        print(d)

def test_daybook():
    print("\n--- Testing Day Book Parser ---")
    filepath = 'raw_daybook_fullyear.xml'
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return
        
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
        
    sanitized = tally_xml.sanitize_xml(raw_bytes)
    result = tally_xml.parse_daybook(sanitized)
    
    sales = result['sales']
    receipts = result['receipts']
    
    print(f"Sales found: {len(sales)}")
    for s in sales:
        print(s)
        
    print(f"Receipts found: {len(receipts)}")
    for r in receipts:
        print(r)

if __name__ == '__main__':
    test_debtors()
    test_daybook()
