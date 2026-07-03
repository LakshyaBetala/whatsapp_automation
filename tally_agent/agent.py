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
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, data=payload, headers={"Content-Type": "text/xml"})
        resp.raise_for_status()
        return resp.content

async def send_to_backend(url: str, endpoint: str, token: str, payload: dict):
    full_url = f"{url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(full_url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

def _fy_start(today: date | None = None) -> date:
    """April 1 of the current Indian financial year."""
    d = today or date.today()
    year = d.year if d.month >= 4 else d.year - 1
    return date(year, 4, 1)


async def run_import(config: dict):
    log_and_print("Starting Initial Outstanding Import...")
    xml_query = tally_xml.build_receivables_query()

    try:
        raw_bytes = await post_to_tally(config['tally_host'], config['tally_port'], xml_query)
        sanitized = tally_xml.sanitize_xml(raw_bytes)
    except Exception as e:
        log_and_print(f"Failed to fetch data from Tally: {e}", is_error=True)
        return

    debtors = tally_xml.parse_debtors(sanitized)
    log_and_print(f"Extracted {len(debtors)} Debtors from Tally.")

    # Shape must match the backend's TallyImportPayload (app/routers/tally.py)
    payload = {
        "business_id": config['business_id'],
        "agent_token": config['agent_token'],
        "company_name": config['company_name'],
        "debtors": [
            {
                "name": d['name'],
                "opening_balance": d['closing_balance'],
                "tally_group": d.get('tally_group', ''),
            }
            for d in debtors
        ],
    }

    try:
        result = await send_to_backend(config['backend_url'], '/tally/import', config['agent_token'], payload)
        log_and_print(f"Successfully pushed initial debtors to backend: {result}")
    except Exception as e:
        log_and_print(f"Failed to push to backend: {e}", is_error=True)

async def run_sync(config: dict):
    log_and_print("Starting Daily Day Book Sync...")
    today = date.today()
    xml_query = tally_xml.build_daybook_query(
        from_date=_fy_start(today).strftime('%Y%m%d'),
        to_date=today.strftime('%Y%m%d'),
    )

    try:
        raw_bytes = await post_to_tally(config['tally_host'], config['tally_port'], xml_query)
        sanitized = tally_xml.sanitize_xml(raw_bytes)
    except Exception as e:
        log_and_print(f"Failed to fetch Day Book from Tally: {e}", is_error=True)
        return

    results = tally_xml.parse_daybook(sanitized)
    sales = results['sales']
    receipts = results['receipts']

    log_and_print(f"Extracted {len(sales)} Sales and {len(receipts)} Receipts from Tally active period.")

    # Shape must match the backend's TallySyncPayload (app/routers/tally.py).
    # Voucher types are normalised to "Sales"/"Receipt" for the backend.
    vouchers = []
    for vtype, records in (("Sales", sales), ("Receipt", receipts)):
        for r in records:
            number = r.get('number') or ''
            if vtype == "Sales" and not number:
                # Sales dedup keys on voucher number — skip unnumbered ones
                log_and_print(f"Skipping unnumbered Sales voucher for {r.get('party')}", is_error=True)
                continue
            if vtype == "Receipt" and not number:
                # Receipts apply FIFO; number is informational only
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
        "company_name": config['company_name'],
        "sync_date": today.isoformat(),
        "vouchers": vouchers,
    }

    try:
        result = await send_to_backend(config['backend_url'], '/tally/sync', config['agent_token'], payload)
        log_and_print(f"Successfully pushed daily sync to backend: {result}")
    except Exception as e:
        log_and_print(f"Failed to push daily sync to backend: {e}", is_error=True)

async def auto_discover_tally(config: dict) -> tuple[str, int]:
    """Attempts to auto-detect a running Tally instance.
    Falls back to config if none found.
    """
    log_and_print("Attempting to auto-discover Tally...")
    
    # Try the config IP first, then localhost on common Tally ports
    endpoints_to_try = [
        (config.get('tally_host', '127.0.0.1'), config.get('tally_port', 9000)),
        ('127.0.0.1', 9000),
        ('localhost', 9000),
        ('127.0.0.1', 90000),
        ('127.0.0.1', 9001),
        ('127.0.0.1', 9009)
    ]
    
    # Minimal valid Tally XML ping
    ping_payload = '<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC><REPORTNAME>List of Accounts</REPORTNAME></REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>'
    
    async with httpx.AsyncClient(timeout=2.0) as client:
        for host, port in endpoints_to_try:
            url = f"http://{host}:{port}"
            try:
                resp = await client.post(url, data=ping_payload, headers={"Content-Type": "text/xml"})
                if resp.status_code == 200 and b"ENVELOPE" in resp.content:
                    log_and_print(f"SUCCESS: Tally auto-discovered at {host}:{port}")
                    return host, port
            except (httpx.ConnectError, httpx.TimeoutException):
                continue
                
    log_and_print("WARNING: Could not auto-discover Tally on local ports. Falling back to config.json.", is_error=True)
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
    
    if args.import_masters:
        asyncio.run(run_import(config))
    if args.sync:
        asyncio.run(run_sync(config))

if __name__ == "__main__":
    main()
