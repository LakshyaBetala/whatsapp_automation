import argparse
import asyncio
import sys
import httpx
import logging
from datetime import date, datetime
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
    async with httpx.AsyncClient(timeout=120.0) as client:
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
    with_phone = sum(1 for d in debtors if d['whatsapp_number'])
    owing = sum(1 for d in debtors if d['opening_balance'] > 0)
    log_and_print(f"Extracted {len(debtors)} customers ({owing} with outstanding, {with_phone} with WhatsApp numbers).")

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

async def run_sync(config: dict):
    log_and_print("Starting Day Book Sync (full financial year, idempotent)...")
    company = config['company_name']

    vouchers_xml = await fetch_and_parse(config, tally_xml.build_vouchers_query(company))
    results = tally_xml.parse_vouchers(vouchers_xml)
    sales = results['sales']
    receipts = results['receipts']
    log_and_print(f"Extracted {len(sales)} Sales and {len(receipts)} Receipts from the active FY.")

    today = date.today()
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

    args = parser.parse_args()

    if not args.import_masters and not args.sync:
        print("Please specify --import-masters or --sync")
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

if __name__ == "__main__":
    main()
