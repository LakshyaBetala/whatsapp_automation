"""Tally XML queries and parsers — no TDL files required.

Everything works over plain HTTP against a stock TallyPrime acting as
server (F1 > Settings > Connectivity). Verified live against
TallyPrime with multiple companies loaded:

  - Collections are scoped per company via SVCURRENTCOMPANY (mandatory
    when >1 company is open — otherwise Tally answers for whichever
    company happens to be active).
  - Ledger ClosingBalance sign: NEGATIVE = customer owes (debit).
  - Voucher collections IGNORE SVFROMDATE/SVTODATE over HTTP and return
    the company's active financial year. Sync therefore sends the whole
    FY and relies on backend idempotency (sales dedup by voucher number,
    receipts dedup by the tally_receipts table).
  - Sales voucher Amount is negative, Receipt positive — use abs().
  - Responses arrive in UTF-16 (BOM) or UTF-8 depending on query type.
"""
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from xml.sax.saxutils import escape


# ── Decoding / cleanup ────────────────────────────────────────────────

def sanitize_xml(raw_bytes: bytes) -> str:
    """Safely decode and sanitize Tally XML.

    Tally mixes encodings per query type: UTF-16 (with BOM) for some
    reports, UTF-8/ASCII for collection exports. Decoding UTF-8 bytes as
    UTF-16 "succeeds" with garbage, so the BOM / leading '<' must decide.
    """
    if raw_bytes[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw_bytes.decode('utf-16', errors='ignore')
    elif raw_bytes[:3] == b'\xef\xbb\xbf':
        text = raw_bytes.decode('utf-8-sig', errors='ignore')
    elif raw_bytes[:1] == b'<' or raw_bytes[:64].lstrip()[:1] == b'<':
        # Plain XML with no BOM — UTF-8 first, cp1252 fallback
        try:
            text = raw_bytes.decode('utf-8')
        except UnicodeDecodeError:
            text = raw_bytes.decode('windows-1252', errors='ignore')
    else:
        for encoding in ('utf-16', 'utf-8', 'windows-1252'):
            try:
                text = raw_bytes.decode(encoding)
                break
            except Exception:
                continue
        else:
            text = raw_bytes.decode('utf-8', errors='ignore')

    # Drop anything before the first '<' (stray BOM remnants / whitespace)
    lt = text.find('<')
    if lt > 0:
        text = text[lt:]

    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'&#x[0-9A-Fa-f]+;', '', text)
    text = re.sub(r'&#\d+;', '', text)
    return text


def parse_tally_date(date_str: str) -> Optional[str]:
    """'YYYYMMDD' -> 'YYYY-MM-DD' (Tally's voucher DATE format)."""
    if not date_str or len(date_str) != 8:
        return None
    try:
        dt = datetime.strptime(date_str, '%Y%m%d')
        return dt.strftime('%Y-%m-%d')
    except ValueError:
        return None


def _to_float(raw: Optional[str]) -> float:
    try:
        return float((raw or '0').replace(',', '').strip() or 0)
    except ValueError:
        return 0.0


# ── Phone extraction ──────────────────────────────────────────────────

def extract_indian_mobile(text: Optional[str]) -> Optional[str]:
    """Pull the first Indian mobile number out of free text.

    Handles '98765 43210', '+91-9876543210', '09876543210', numbers buried
    in an address line, etc. Returns normalised '91XXXXXXXXXX' or None.
    Mobile numbers start 6-9; 6-digit pincodes and house numbers don't match.
    """
    if not text:
        return None
    cleaned = re.sub(r'[\s\-\(\)\.\/]', '', text)
    m = re.search(r'(?:\+?91|0)?([6-9]\d{9})(?!\d)', cleaned)
    if not m:
        return None
    return '91' + m.group(1)


def parse_credit_days(raw: Optional[str]) -> Optional[int]:
    """'45 Days' / '45' -> 45. Tolerates junk ('13-Jan-2021' etc.) -> None."""
    if not raw:
        return None
    m = re.fullmatch(r'\s*(\d{1,3})\s*(?:Days?)?\s*', raw, re.IGNORECASE)
    if not m:
        return None
    days = int(m.group(1))
    return days if 0 < days <= 365 else None


def _phone_from_ledger(ledger: ET.Element) -> Optional[str]:
    """Best mobile for a LEDGER element: dedicated fields first, then
    address lines (many shops type the mobile into the address)."""
    candidates = [
        ledger.findtext('LEDGERMOBILE', ''),
        ledger.findtext('LEDGERPHONE', ''),
        ledger.findtext('LEDGERCONTACT', ''),
    ]
    for addr in ledger.iter('ADDRESS'):
        if addr.text:
            candidates.append(addr.text)
    for candidate in candidates:
        phone = extract_indian_mobile(candidate)
        if phone:
            return phone
    return None


# ── Query builders (inline TDL collections — no .tdl files needed) ────

def _collection_query(objtype: str, methods: List[str], company: str = "") -> str:
    sv_company = f'<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>' if company else ''
    native = ''.join(f'<NATIVEMETHOD>{m}</NATIVEMETHOD>' for m in methods)
    return f'''<ENVELOPE>
    <HEADER>
      <VERSION>1</VERSION>
      <TALLYREQUEST>Export</TALLYREQUEST>
      <TYPE>COLLECTION</TYPE>
      <ID>SaaS{objtype}s</ID>
    </HEADER>
    <BODY>
      <DESC>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          {sv_company}
        </STATICVARIABLES>
        <TDL>
          <TDLMESSAGE>
            <COLLECTION NAME="SaaS{objtype}s" ISMODIFY="No">
              <TYPE>{objtype}</TYPE>
              {native}
            </COLLECTION>
          </TDLMESSAGE>
        </TDL>
      </DESC>
    </BODY>
  </ENVELOPE>'''


def build_groups_query(company: str = "") -> str:
    """All account groups (name + parent) — used to find every subgroup
    under Sundry Debtors (shops group customers by street/route)."""
    return _collection_query('Group', ['Name', 'Parent'], company)


def build_masters_query(company: str = "") -> str:
    """All ledgers with balances + contact + credit-period fields in one shot."""
    return _collection_query('Ledger', [
        'Name', 'Parent', 'OpeningBalance', 'ClosingBalance',
        'LedgerPhone', 'LedgerMobile', 'LedgerContact', 'Address',
        'BillCreditPeriod',
    ], company)


def build_vouchers_query(company: str = "") -> str:
    """All vouchers of the CURRENT-DATE window (collection queries ignore
    date filters). Kept for diagnostics — sync uses the Voucher Register."""
    return _collection_query('Voucher', [
        'Date', 'VoucherTypeName', 'VoucherNumber', 'PartyLedgerName', 'Amount',
    ], company)


def build_voucher_register_query(company: str, from_date: str, to_date: str) -> str:
    """Voucher Register report WITH working date filters.

    Unlike collections and Day Book, this report honours SVFROMDATE/
    SVTODATE (the TYPE="Date" attribute matters) — verified against
    archived-FY companies whose books are far from today's date.
    Dates are 'YYYYMMDD'.
    """
    sv_company = f'<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>' if company else ''
    return f'''<ENVELOPE>
    <HEADER>
      <VERSION>1</VERSION>
      <TALLYREQUEST>EXPORT</TALLYREQUEST>
      <TYPE>DATA</TYPE>
      <ID>Voucher Register</ID>
    </HEADER>
    <BODY>
      <DESC>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          {sv_company}
          <SVFROMDATE TYPE="Date">{from_date}</SVFROMDATE>
          <SVTODATE TYPE="Date">{to_date}</SVTODATE>
        </STATICVARIABLES>
      </DESC>
    </BODY>
  </ENVELOPE>'''


def build_company_list_query() -> str:
    return _collection_query('Company', ['Name'])


def build_bills_query(company: str = "") -> str:
    """Bill-by-bill OUTSTANDING — Tally's own net figure per open bill.

    This is the authoritative source of what each party owes (net of every
    payment, however it was booked) plus each bill's real date — so overdue
    days and amounts are exact, instead of the fragile
    opening+sales-receipts re-derivation. Requires the party ledgers to
    'maintain balances bill-by-bill' (the norm for wholesale debtors).
    """
    return _collection_query('Bills', [
        'Name', 'Parent', 'BillDate', 'ClosingBalance', 'BillCreditPeriod', 'BillType',
    ], company)


# ── Parsers ───────────────────────────────────────────────────────────

def _elem_name(elem: ET.Element) -> str:
    return (elem.attrib.get('NAME', '') or elem.findtext('NAME', '') or '').strip()


def parse_companies(xml_text: str) -> List[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [n for n in (_elem_name(c) for c in root.iter('COMPANY')) if n]


def parse_groups(xml_text: str) -> Dict[str, str]:
    """Group name -> parent group name."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out: Dict[str, str] = {}
    for g in root.iter('GROUP'):
        name = _elem_name(g)
        if name:
            out[name] = (g.findtext('PARENT', '') or '').strip()
    return out


def debtor_group_names(group_parent: Dict[str, str]) -> Set[str]:
    """Every group that descends from 'Sundry Debtors' (plus the root)."""
    def under(g: str) -> bool:
        seen = set()
        while g and g not in seen:
            if g.strip().lower() == 'sundry debtors':
                return True
            seen.add(g)
            g = group_parent.get(g, '')
        return False

    result = {g for g in group_parent if under(g)}
    result.add('Sundry Debtors')
    return result


def parse_masters(xml_text: str, debtor_groups: Set[str]) -> List[Dict[str, Any]]:
    """Debtor ledgers with balances, group and phone.

    Sign convention OUT of here: positive = customer owes (Tally stores
    debit balances as negative, so we flip).

    opening_balance is the FY-START balance (Tally OpeningBalance), NOT
    today's closing — the sync replays this FY's vouchers on top of it,
    so using the closing balance would double-count this year's sales.
    current_outstanding (today's closing) is informational.
    Zero-balance clients are kept — they get bills later and we want their
    phone numbers on file.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    lowered = {g.strip().lower() for g in debtor_groups}
    debtors: List[Dict[str, Any]] = []
    for led in root.iter('LEDGER'):
        name = _elem_name(led)
        parent = (led.findtext('PARENT', '') or '').strip()
        if not name or parent.strip().lower() not in lowered:
            continue
        opening = _to_float(led.findtext('OPENINGBALANCE', '0'))
        closing = _to_float(led.findtext('CLOSINGBALANCE', '0'))
        debtors.append({
            'name': name,
            'tally_group': parent,
            'opening_balance': -opening,      # FY-start balance, owes -> positive
            'current_outstanding': -closing,  # today's balance (informational)
            'whatsapp_number': _phone_from_ledger(led),
            'credit_days': parse_credit_days(led.findtext('BILLCREDITPERIOD', '')),
        })
    return debtors


def parse_ledger_contacts(xml_text: str) -> Dict[str, str]:
    """Map ledger name -> '91XXXXXXXXXX' (kept for tests/diagnostics)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    contacts: Dict[str, str] = {}
    for ledger in root.iter('LEDGER'):
        name = _elem_name(ledger)
        if not name:
            continue
        phone = _phone_from_ledger(ledger)
        if phone:
            contacts[name] = phone
    return contacts


def _parse_flexible_date(raw: Optional[str]) -> Optional[str]:
    """Tally bill dates arrive as '20210401' or '1-Apr-21' / '1-Apr-2021'.
    Return 'YYYY-MM-DD' or None."""
    raw = (raw or '').strip()
    if not raw:
        return None
    if re.fullmatch(r'\d{8}', raw):
        return parse_tally_date(raw)
    for fmt in ('%d-%b-%Y', '%d-%b-%y', '%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def parse_bills(xml_text: str, debtor_ledgers: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    """Parse the bill-by-bill outstanding collection.

    Returns one dict per OPEN bill a debtor owes:
      {party, bill_ref, bill_date, amount, credit_days, due_date}

    ``amount`` is Tally's ClosingBalance for that bill, sign-flipped so a
    debit (owed) is positive; credit/advance bills (<=0) are dropped.
    If ``debtor_ledgers`` is given, only bills whose parent ledger is in it
    are kept (creditors excluded); otherwise all owed bills are returned.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    lowered = {n.strip().lower() for n in debtor_ledgers} if debtor_ledgers else None
    out: List[Dict[str, Any]] = []
    for b in root.iter('BILL'):
        party = (b.findtext('PARENT', '') or '').strip()
        if lowered is not None and party.lower() not in lowered:
            continue
        amount = -_to_float(b.findtext('CLOSINGBALANCE', '0'))  # owed -> positive
        if amount <= 0:
            continue
        bill_date = _parse_flexible_date(b.findtext('BILLDATE', ''))
        credit = parse_credit_days(b.findtext('BILLCREDITPERIOD', ''))
        due = None
        if bill_date and credit is not None:
            due = (datetime.strptime(bill_date, '%Y-%m-%d')
                   + timedelta(days=credit)).strftime('%Y-%m-%d')
        out.append({
            'party': party,
            'bill_ref': (_elem_name(b) or (b.findtext('NAME', '') or '').strip()),
            'bill_date': bill_date,
            'amount': round(amount, 2),
            'credit_days': credit,
            'due_date': due or bill_date,
        })
    return out


def parse_vouchers(xml_text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Split the FY voucher dump into sales and receipts.

    Matches by containment ('GST Sales' etc. count as sales); Purchase,
    Payment, Contra, Journal and cheque-return types are ignored.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {'sales': [], 'receipts': []}

    sales: List[Dict[str, Any]] = []
    receipts: List[Dict[str, Any]] = []
    for v in root.iter('VOUCHER'):
        vtype = (v.findtext('VOUCHERTYPENAME', '') or '').strip()
        party = (v.findtext('PARTYLEDGERNAME', '') or '').strip()
        formatted_date = parse_tally_date((v.findtext('DATE', '') or '').strip())
        number = (v.findtext('VOUCHERNUMBER', '') or '').strip()

        if (v.findtext('ISCANCELLED', '') or '').strip().lower() == 'yes':
            continue
        if (v.findtext('ISOPTIONAL', '') or '').strip().lower() == 'yes':
            continue

        # Collection exports put the total in AMOUNT; the Voucher Register
        # report leaves it empty and carries amounts in ledger-entry lists —
        # the party's own entry is the invoice/receipt total.
        amount = abs(_to_float(v.findtext('AMOUNT', '0')))
        if amount == 0:
            best = 0.0
            for tag in ('ALLLEDGERENTRIES.LIST', 'LEDGERENTRIES.LIST'):
                for le in v.iter(tag):
                    lname = (le.findtext('LEDGERNAME', '') or '').strip()
                    lamt = abs(_to_float(le.findtext('AMOUNT', '0')))
                    if lname == party and lamt:
                        amount = lamt
                        break
                    best = max(best, lamt)
                if amount:
                    break
            if amount == 0:
                amount = best  # fallback: largest entry (party name mismatch)

        if not party or not formatted_date or amount == 0:
            continue

        record = {
            'number': number,
            'party': party,
            'date': formatted_date,
            'amount': amount,
        }
        lower = vtype.lower()
        if 'sales' in lower:
            sales.append(record)
        elif 'receipt' in lower:
            receipts.append(record)

    return {'sales': sales, 'receipts': receipts}
