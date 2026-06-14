import xml.etree.ElementTree as ET

tree = ET.parse('tally_daybook.xml')
root = tree.getroot()

# Find the first sales voucher
voucher = root.find('.//VOUCHER[@VCHTYPE="Sales"]')
if voucher is None:
    print("No sales vouchers found.")
else:
    print(f"Voucher Date: {voucher.findtext('DATE')}")
    print(f"Voucher Number: {voucher.findtext('VOUCHERNUMBER')}")
    print(f"Party Name: {voucher.findtext('PARTYLEDGERNAME')}")
    
    print("\nLedger Entries:")
    for ledger in voucher.findall('.//ALLLEDGERENTRIES.LIST'):
        name = ledger.findtext('LEDGERNAME')
        amt = ledger.findtext('AMOUNT')
        print(f"  Ledger: {name} | Amount: {amt}")
        
    print("\nInventory Entries:")
    for inv in voucher.findall('.//ALLINVENTORYENTRIES.LIST'):
        item = inv.findtext('STOCKITEMNAME')
        amt = inv.findtext('AMOUNT')
        print(f"  Item: {item} | Amount: {amt}")
