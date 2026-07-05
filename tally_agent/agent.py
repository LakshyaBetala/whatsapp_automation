import argparse
import asyncio
import calendar
import sys
import httpx
import logging
from datetime import date, datetime, timedelta
from config import load_config
import tally_xml

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
        log_and_print("No debtors found — is the right company open in Tally?", is_error=True)
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
    # (YYYY-MM-DD) in config.json overrides — needed for companies whose
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
            log_and_print(f"  {chunk_start:%b %Y}: fetch failed ({e}) — continuing", is_error=True)
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


def _vouchers_payload(sales: list, receipts: list) -> list:
    """Map parsed vouchers to the backend's TallySyncPayload shape."""
    vouchers = []
    for vtype, records in (("Sales", sales), ("Receipt", receipts)):
        for r in records:
            number = r.get('number') or ''
            if vtype == "Sales" and not number:
                # Sales dedup keys on voucher number — skip unnumbered ones
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
    """Live mode: poll Tally every watch_interval_seconds and push new
    vouchers immediately — a bill made in Tally reaches the customer's
    WhatsApp (PDF + message) within ~2 minutes.

    Each cycle fetches only the last 3 days (light on Tally); the backend
    is idempotent, so repeats are harmless. The nightly --sync still runs
    the full FY to catch backdated entries.
    """
    interval = int(config.get('watch_interval_seconds', 120) or 120)
    company = config['company_name']
    log_and_print(f"WATCH MODE: checking Tally every {interval}s for new bills/payments. Ctrl+C to stop.")

    failures = 0
    while True:
        today = date.today()
        frm = (today - timedelta(days=2)).strftime('%Y%m%d')
        try:
            xml = await fetch_and_parse(
                config, tally_xml.build_voucher_register_query(company, frm, today.strftime('%Y%m%d')))
            r = tally_xml.parse_vouchers(xml)
            vouchers = _vouchers_payload(r['sales'], r['receipts'])
            if vouchers:
                payload = {
                    "business_id": config['business_id'],
                    "agent_token": config['agent_token'],
                    "company_name": company,
                    "sync_date": today.isoformat(),
                    "vouchers": vouchers,
                }
                result = await send_to_backend(config['backend_url'], '/tally/sync', config['agent_token'], payload)
                if result.get('new_bills') or result.get('receipts_processed'):
                    log_and_print(
                        f"NEW: {result.get('new_bills', 0)} bill(s) sent to WhatsApp, "
                        f"{result.get('receipts_processed', 0)} payment(s) applied.")
            failures = 0
        except KeyboardInterrupt:
            raise
        except Exception as e:
            failures += 1
            log_and_print(f"Watch cycle failed ({e}) — retrying in {interval}s", is_error=True)
            if failures in (5, 50):  # don't spam; nudge at 10min and ~2h of failures
                log_and_print("Tally or backend unreachable for a while — check they are running.", is_error=True)
        await asyncio.sleep(interval)


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

    args = parser.parse_args()

    if not args.import_masters and not args.sync and not args.watch:
        print("Please specify --import-masters, --sync or --watch")
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
