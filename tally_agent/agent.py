import argparse
import asyncio
import base64
import calendar
import os
import shutil
import sys
import httpx
import logging
from datetime import date, datetime, timedelta
from config import load_config
import tally_xml


def _attach_tally_pdfs(vouchers: list, pdf_dir: str) -> int:
    """For new Sales vouchers, attach Tally's own exported invoice PDF from
    pdf_dir (as base64) so the customer receives the EXACT Tally bill.

    Robust to filename format: tries Sales_<voucher>.pdf and <voucher>.pdf
    first, then falls back to ANY .pdf in the folder whose name contains the
    voucher number (case-insensitive). Returns how many were attached."""
    if not pdf_dir or not os.path.isdir(pdf_dir):
        return 0
    try:
        files = [f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf')]
    except Exception:
        return 0
    by_lower = {f.lower(): f for f in files}
    attached = 0
    for v in vouchers:
        if v.get('voucher_type') != 'Sales':
            continue
        num = (v.get('voucher_number') or '').strip()
        if not num:
            continue
        match = None
        for name in (f"Sales_{num}.pdf", f"{num}.pdf", f"Sales_{num.lower()}.pdf"):
            if name.lower() in by_lower:
                match = by_lower[name.lower()]
                break
        if not match:                      # fallback: any pdf containing the voucher no
            nl = num.lower()
            match = next((f for f in files if nl in f.lower()), None)
        if match:
            try:
                src = os.path.join(pdf_dir, match)
                with open(src, 'rb') as fh:
                    v['pdf_base64'] = base64.b64encode(fh.read()).decode('ascii')
                v['_pdf_src'] = src   # remembered so run_watch can move it after send
                attached += 1
            except Exception:
                pass
    return attached

# Setup file logging
logging.basicConfig(
    filename='agent.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_and_print(msg: str, is_error=False):
    if is_error:
        logging.error(msg)
        print(f"ERROR: {msg}")
    else:
        logging.info(msg)
        print(msg)

async def post_to_tally(host: str, port: int, payload: str) -> bytes:
    url = f"http://{host}:{port}"
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, data=payload, headers={"Content-Type": "text/xml"})
        resp.raise_for_status()
        return resp.content

async def fetch_and_parse(config: dict, query: str) -> str:
    raw = await post_to_tally(config['tally_host'], config['tally_port'], query)
    return tally_xml.sanitize_xml(raw)

async def send_to_backend(url: str, endpoint: str, token: str, payload: dict):
    full_url = f"{url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(full_url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

async def check_company(config: dict):
    """Warn early if the configured company is not loaded in Tally."""
    try:
        xml = await fetch_and_parse(config, tally_xml.build_company_list_query())
        companies = tally_xml.parse_companies(xml)
        if companies and config['company_name'] not in companies:
            log_and_print(
                f"WARNING: '{config['company_name']}' not in Tally's open companies: {companies}. "
                f"Open it in Tally or fix 'company_name' in config.json.", is_error=True)
        elif companies:
            log_and_print(f"Company OK: {config['company_name']} (of {len(companies)} open)")
    except Exception as e:
        log_and_print(f"Could not verify company list (continuing): {e}", is_error=True)

async def run_import(config: dict):
    log_and_print("Starting Initial Outstanding Import (no TDL needed)...")
    company = config['company_name']

    # 1. Group tree -> which groups hold customers (street/route subgroups)
    groups_xml = await fetch_and_parse(config, tally_xml.build_groups_query(company))
    group_parent = tally_xml.parse_groups(groups_xml)
    debtor_groups = tally_xml.debtor_group_names(group_parent)
    log_and_print(f"Found {len(debtor_groups)} customer groups under Sundry Debtors.")

    # 2. All ledgers -> debtors with balances + phone numbers
    masters_xml = await fetch_and_parse(config, tally_xml.build_masters_query(company))
    debtors = tally_xml.parse_masters(masters_xml, debtor_groups)

    # Credit terms: Tally's per-ledger BillCreditPeriod wins; else the
    # shop-wide default from config.json (e.g. a 45-days trade), else the
    # backend default (30).
    shop_default = config.get('default_credit_days')
    if shop_default:
        for d in debtors:
            if d.get('credit_days') is None:
                d['credit_days'] = int(shop_default)

    with_phone = sum(1 for d in debtors if d['whatsapp_number'])
    with_terms = sum(1 for d in debtors if d.get('credit_days'))
    owing_now = sum(1 for d in debtors if d.get('current_outstanding', 0) > 0)
    ob = sum(1 for d in debtors if d['opening_balance'] > 0)
    log_and_print(
        f"Extracted {len(debtors)} customers ({ob} with FY-opening balance, "
        f"{owing_now} owing today, {with_phone} with WhatsApp numbers, {with_terms} with credit terms).")
    log_and_print("Note: run --sync after import to bring in this FY's bills and payments.")

    if not debtors:
        log_and_print("No debtors found - is the right company open in Tally?", is_error=True)
        return

    payload = {
        "business_id": config['business_id'],
        "agent_token": config['agent_token'],
        "company_name": company,
        "debtors": debtors,
    }

    try:
        result = await send_to_backend(config['backend_url'], '/tally/import', config['agent_token'], payload)
        log_and_print(f"Backend result: {result}")
    except Exception as e:
        log_and_print(f"Failed to push to backend: {e}", is_error=True)

def _fy_start(today: date) -> date:
    """April 1 of the current Indian financial year."""
    year = today.year if today.month >= 4 else today.year - 1
    return date(year, 4, 1)


async def run_sync(config: dict):
    company = config['company_name']
    today = date.today()

    # Sync window: FY start .. today by default. 'sync_from_date'
    # (YYYY-MM-DD) in config.json overrides - needed for companies whose
    # books belong to an earlier financial year.
    from_cfg = str(config.get('sync_from_date', '') or '').replace('-', '')
    from_str = from_cfg if len(from_cfg) == 8 else _fy_start(today).strftime('%Y%m%d')
    start = datetime.strptime(from_str, '%Y%m%d').date()
    log_and_print(f"Starting sync via Voucher Register ({from_str}..{today:%Y%m%d}, idempotent)...")

    # Fetch month-by-month: big wholesalers book thousands of vouchers a
    # month and a full-FY export times Tally out.
    sales, receipts = [], []
    chunk_start = start
    while chunk_start <= today:
        last_day = calendar.monthrange(chunk_start.year, chunk_start.month)[1]
        chunk_end = min(date(chunk_start.year, chunk_start.month, last_day), today)
        try:
            xml = await fetch_and_parse(
                config,
                tally_xml.build_voucher_register_query(
                    company, chunk_start.strftime('%Y%m%d'), chunk_end.strftime('%Y%m%d')))
            r = tally_xml.parse_vouchers(xml)
            sales.extend(r['sales'])
            receipts.extend(r['receipts'])
            log_and_print(f"  {chunk_start:%b %Y}: {len(r['sales'])} sales, {len(r['receipts'])} receipts")
        except Exception as e:
            log_and_print(f"  {chunk_start:%b %Y}: fetch failed ({e}) - continuing", is_error=True)
        chunk_start = chunk_end + timedelta(days=1)

    log_and_print(f"Extracted {len(sales)} Sales and {len(receipts)} Receipts total.")

    if not sales and not receipts:
        log_and_print(
            "0 vouchers in this window. If this company's books are from an "
            "earlier FY, set \"sync_from_date\": \"YYYY-MM-DD\" in config.json.",
            is_error=True)
        return

    vouchers = _vouchers_payload(sales, receipts)

    payload = {
        "business_id": config['business_id'],
        "agent_token": config['agent_token'],
        "company_name": company,
        "sync_date": today.isoformat(),
        "vouchers": vouchers,
    }

    try:
        result = await send_to_backend(config['backend_url'], '/tally/sync', config['agent_token'], payload)
        log_and_print(f"Backend result: {result}")
    except Exception as e:
        log_and_print(f"Failed to push daily sync to backend: {e}", is_error=True)

    # Authoritative accuracy pass: overwrite outstanding with Tally's bill-wise
    # net figures + real dates (must run AFTER the voucher replay above).
    await run_apply_outstanding(config)


def _vouchers_payload(sales: list, receipts: list) -> list:
    """Map parsed vouchers to the backend's TallySyncPayload shape."""
    vouchers = []
    for vtype, records in (("Sales", sales), ("Receipt", receipts)):
        for r in records:
            number = r.get('number') or ''
            if vtype == "Sales" and not number:
                # Sales dedup keys on voucher number - skip unnumbered ones
                log_and_print(f"Skipping unnumbered Sales voucher for {r.get('party')}", is_error=True)
                continue
            if vtype == "Receipt" and not number:
                number = f"RCPT-{r.get('party', '')[:20]}-{r.get('date', '')}"
            vouchers.append({
                "voucher_number": number,
                "voucher_type": vtype,
                "party_name": r['party'],
                "amount": r['amount'],
                "date": r['date'],
            })
    return vouchers


async def run_watch(config: dict):
    """Live LOCAL mode: poll Tally on this laptop every watch_interval_seconds
    (default 300 = 5 min). A bill made in Tally reaches the customer's WhatsApp
    (PDF + message) within one cycle - so a 9:00 bill goes out by ~9:05.

    Each cycle fetches only the last 3 days (light on Tally). Every few cycles
    it ALSO refreshes the authoritative bill-wise outstanding (accuracy). Every
    Tally call is made sequentially inside THIS single loop, so Tally (whose
    HTTP server is single-threaded) never receives concurrent/overlapping
    requests - the main cause of it wedging.
    """
    interval = int(config.get('watch_interval_seconds', 300) or 300)
    company = config['company_name']
    # Make sure the Tally PDF pickup folder exists (the TDL exports bills here).
    pdf_dir = config.get('bill_pdf_dir', '')
    if pdf_dir:
        try:
            os.makedirs(pdf_dir, exist_ok=True)
        except Exception as e:
            log_and_print(f"Could not create bill_pdf_dir {pdf_dir}: {e}", is_error=True)
    # Full outstanding sync every N cycles. Default 1 = EVERY cycle, so the
    # dashboard mirrors live Tally every 5 min (new bills + every party's exact
    # amount/dates). All Tally calls stay sequential inside this one loop, so
    # Tally never receives concurrent requests (its server is single-threaded).
    refresh_cycles = max(1, int(config.get('refresh_every_cycles', 1) or 1))
    log_and_print(f"WATCH MODE (local): every {interval}s -> deliver new bills + full "
                  f"outstanding sync (every {refresh_cycles} cycle). Ctrl+C to stop.")

    failures = 0
    cycle = 0
    while True:
        stamp = datetime.now().strftime('%H:%M:%S')
        try:
            # Accuracy refresh on first cycle and periodically - sequential, so
            # it never overlaps the light check below.
            if cycle % refresh_cycles == 0:
                await run_apply_outstanding(config)

            today = date.today()
            frm = (today - timedelta(days=2)).strftime('%Y%m%d')
            xml = await fetch_and_parse(
                config, tally_xml.build_voucher_register_query(company, frm, today.strftime('%Y%m%d')))
            r = tally_xml.parse_vouchers(xml)
            vouchers = _vouchers_payload(r['sales'], r['receipts'])
            # Attach Tally's own exported PDFs (from the pickup folder), and
            # remember each PDF's source path for cleanup after it is sent.
            _attach_tally_pdfs(vouchers, pdf_dir)
            pdf_srcs = {}
            for v in vouchers:
                src = v.pop('_pdf_src', None)   # keep it OUT of the wire payload
                if src:
                    pdf_srcs[v.get('voucher_number')] = src
            new_bills = payments = 0
            if vouchers:
                payload = {
                    "business_id": config['business_id'],
                    "agent_token": config['agent_token'],
                    "company_name": company,
                    "sync_date": today.isoformat(),
                    "vouchers": vouchers,
                }
                result = await send_to_backend(config['backend_url'], '/tally/sync', config['agent_token'], payload)
                new_bills = result.get('new_bills', 0)
                payments = result.get('receipts_processed', 0)
                # Move sent bills' PDFs into <folder>/sent so the pickup folder
                # stays clean - no re-upload, no stale file matching a later bill.
                delivered = set(result.get('delivered') or [])
                if delivered and pdf_dir and pdf_srcs:
                    sent_dir = os.path.join(pdf_dir, 'sent')
                    try:
                        os.makedirs(sent_dir, exist_ok=True)
                        for vnum in delivered:
                            src = pdf_srcs.get(vnum)
                            if src and os.path.exists(src):
                                shutil.move(src, os.path.join(sent_dir, os.path.basename(src)))
                    except Exception as e:
                        log_and_print(f"PDF cleanup skipped: {e}", is_error=True)
            # Heartbeat every cycle so the Status log SHOWS the sync running.
            log_and_print(f"[{stamp}] Tally checked (local) - {new_bills} new bill(s) sent, {payments} payment(s).")
            failures = 0
        except KeyboardInterrupt:
            raise
        except Exception as e:
            failures += 1
            log_and_print(f"[{stamp}] Watch cycle failed ({e}) - retry in {interval}s", is_error=True)
            if failures in (5, 50):  # don't spam; nudge at 10min and ~2h of failures
                log_and_print("Tally or backend unreachable for a while - check they are running.", is_error=True)
        cycle += 1
        await asyncio.sleep(interval)


async def run_check_outstanding(config: dict):
    """PREVIEW: pull Tally's bill-by-bill OUTSTANDING and print per-party
    totals, so we can confirm the amounts match Tally before wiring this
    authoritative source into the dashboard. Changes nothing."""
    company = config['company_name']
    today = date.today()
    log_and_print("Fetching bill-by-bill outstanding from Tally (PREVIEW - nothing will change)...")

    groups_xml = await fetch_and_parse(config, tally_xml.build_groups_query(company))
    debtor_groups = tally_xml.debtor_group_names(tally_xml.parse_groups(groups_xml))
    masters_xml = await fetch_and_parse(config, tally_xml.build_masters_query(company))
    debtors = tally_xml.parse_masters(masters_xml, debtor_groups)
    debtor_names = {d['name'] for d in debtors}
    ledger_close = {d['name']: d.get('current_outstanding', 0) for d in debtors}

    bills_xml = await fetch_and_parse(config, tally_xml.build_bills_query(company))
    bills = tally_xml.parse_bills(bills_xml, debtor_names)

    if not bills:
        log_and_print(
            "No bill-by-bill data came back. The ledgers may not 'maintain balances "
            "bill-by-bill'. Tell me - we can fall back to each ledger's ClosingBalance "
            "(accurate total, but no per-bill dates).", is_error=True)
        return

    from collections import defaultdict
    by_party: dict = defaultdict(list)
    for b in bills:
        by_party[b['party']].append(b)

    total = sum(b['amount'] for b in bills)
    ledger_total = sum(v for v in ledger_close.values() if v and v > 0)
    log_and_print(f"Got {len(bills)} open bills across {len(by_party)} parties.")
    log_and_print(f"TOTAL outstanding (bill-wise) = {total:,.0f}   |   ledger ClosingBalance total = {ledger_total:,.0f}")
    log_and_print("Top 15 parties (bill-wise total; flag if it disagrees with ledger closing):")
    ranked = sorted(by_party.items(), key=lambda kv: sum(x['amount'] for x in kv[1]), reverse=True)
    for party, bl in ranked[:15]:
        s = sum(x['amount'] for x in bl)
        close = ledger_close.get(party, 0)
        flag = "" if abs(s - close) < 1 else f"   << ledger says {close:,.0f}"
        log_and_print(f"  {party[:36]:36} {s:>12,.0f}  ({len(bl)} bills){flag}")
    p, bl = ranked[0]
    log_and_print(f"Sample bills for '{p}':")
    for x in sorted(bl, key=lambda z: z['bill_date'] or '')[:8]:
        try:
            od = (today - datetime.strptime(x['due_date'], '%Y-%m-%d').date()).days if x['due_date'] else '?'
        except ValueError:
            od = '?'
        log_and_print(f"    ref={str(x['bill_ref'])[:16]:16} date={x['bill_date']} "
                      f"amt={x['amount']:>10,.0f} credit={x['credit_days']} overdue={od}d")
    log_and_print("PREVIEW done - nothing changed. Verify PINEMA/MAHALAKSHMI totals match Tally, then we wire it in.")


async def run_apply_outstanding(config: dict):
    """Push Tally's bill-by-bill outstanding to the backend as the source of
    truth for amounts + dates (fixes receipt-capture drift). Runs at the end
    of --sync and via --refresh-outstanding."""
    company = config['company_name']
    groups_xml = await fetch_and_parse(config, tally_xml.build_groups_query(company))
    debtor_groups = tally_xml.debtor_group_names(tally_xml.parse_groups(groups_xml))
    masters_xml = await fetch_and_parse(config, tally_xml.build_masters_query(company))
    debtors = tally_xml.parse_masters(masters_xml, debtor_groups)
    debtor_names = sorted({d['name'] for d in debtors})

    bills_xml = await fetch_and_parse(config, tally_xml.build_bills_query(company))
    bills = tally_xml.parse_bills(bills_xml, set(debtor_names))
    if not bills:
        log_and_print("No bill-by-bill data from Tally - outstanding NOT refreshed "
                      "(ledgers may not maintain balances bill-by-bill).", is_error=True)
        return

    payload = {
        "business_id": config['business_id'],
        "agent_token": config['agent_token'],
        "company_name": company,
        "bills": [{
            "party_name": b['party'], "bill_ref": b['bill_ref'],
            "bill_date": b['bill_date'], "due_date": b['due_date'], "amount": b['amount'],
        } for b in bills],
        "all_parties": debtor_names,
    }
    log_and_print(f"Refreshing outstanding from Tally bill-wise: {len(bills)} bills...")
    try:
        result = await send_to_backend(config['backend_url'], '/tally/outstandings', config['agent_token'], payload)
        log_and_print(f"Outstanding refreshed: {result}")
    except Exception as e:
        log_and_print(f"Failed to refresh outstanding: {e}", is_error=True)


async def auto_discover_tally(config: dict) -> tuple[str, int]:
    """Attempts to auto-detect a running Tally instance.
    Falls back to config if none found.
    """
    log_and_print("Attempting to auto-discover Tally...")

    endpoints_to_try = [
        (config.get('tally_host', '127.0.0.1'), config.get('tally_port', 9000)),
        ('127.0.0.1', 9000),
        ('localhost', 9000),
        ('127.0.0.1', 9001),
        ('127.0.0.1', 9009)
    ]

    ping_payload = tally_xml.build_company_list_query()

    async with httpx.AsyncClient(timeout=3.0) as client:
        for host, port in endpoints_to_try:
            url = f"http://{host}:{port}"
            try:
                resp = await client.post(url, data=ping_payload, headers={"Content-Type": "text/xml"})
                if resp.status_code == 200 and b"ENVELOPE" in resp.content:
                    log_and_print(f"SUCCESS: Tally found at {host}:{port}")
                    return host, port
            except (httpx.ConnectError, httpx.TimeoutException):
                continue

    log_and_print("WARNING: Could not auto-discover Tally. Falling back to config.json.", is_error=True)
    return config['tally_host'], config['tally_port']

def main():
    parser = argparse.ArgumentParser(description="Tally Sync Agent")
    parser.add_argument('--import-masters', action='store_true', help='Run one-time import of all debtors')
    parser.add_argument('--sync', action='store_true', help='Run daily sync of Day Book')
    parser.add_argument('--watch', action='store_true', help='Live mode: push new bills to WhatsApp within ~2 minutes')
    parser.add_argument('--check-outstanding', action='store_true', help='PREVIEW bill-by-bill outstanding from Tally (changes nothing)')
    parser.add_argument('--refresh-outstanding', action='store_true', help='Apply Tally bill-by-bill outstanding as the source of truth (accurate amounts + dates)')

    args = parser.parse_args()

    if not (args.import_masters or args.sync or args.watch or args.check_outstanding or args.refresh_outstanding):
        print("Please specify --import-masters, --sync, --watch, --check-outstanding or --refresh-outstanding")
        sys.exit(1)

    config = load_config()

    # Auto-discover host and port
    host, port = asyncio.run(auto_discover_tally(config))
    config['tally_host'] = host
    config['tally_port'] = port

    log_and_print(f"Connecting to Tally at {config['tally_host']}:{config['tally_port']}")
    log_and_print(f"Backend URL: {config['backend_url']}")
    log_and_print(f"Company: {config['company_name']}")

    asyncio.run(check_company(config))

    if args.check_outstanding:
        asyncio.run(run_check_outstanding(config))
    if args.refresh_outstanding:
        asyncio.run(run_apply_outstanding(config))
    if args.import_masters:
        asyncio.run(run_import(config))
    if args.sync:
        asyncio.run(run_sync(config))
    if args.watch:
        try:
            asyncio.run(run_watch(config))
        except KeyboardInterrupt:
            log_and_print("Watch stopped.")

if __name__ == "__main__":
    main()
