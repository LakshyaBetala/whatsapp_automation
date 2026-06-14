import argparse
import asyncio
import httpx
import logging
from datetime import datetime
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

async def run_import(config: dict):
    log_and_print("Starting Initial Outstanding Import...")
    xml_query = tally_xml.build_all_masters_query()
    
    try:
        raw_bytes = await post_to_tally(config['tally_host'], config['tally_port'], xml_query)
        sanitized = tally_xml.sanitize_xml(raw_bytes)
    except Exception as e:
        log_and_print(f"Failed to fetch data from Tally: {e}", is_error=True)
        return

    debtors = tally_xml.parse_debtors(sanitized)
    log_and_print(f"Extracted {len(debtors)} Debtors from Tally.")
    
    payload = {
        "business_id": config['business_id'],
        "clients": debtors
    }
    
    try:
        await send_to_backend(config['backend_url'], '/tally/import', config['agent_token'], payload)
        log_and_print("Successfully pushed initial debtors to backend.")
    except Exception as e:
        log_and_print(f"Failed to push to backend: {e}", is_error=True)

async def run_sync(config: dict):
    log_and_print("Starting Daily Day Book Sync...")
    xml_query = tally_xml.build_daybook_query()
    
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
    
    payload = {
        "business_id": config['business_id'],
        "sales": sales,
        "receipts": receipts
    }
    
    try:
        await send_to_backend(config['backend_url'], '/tally/sync', config['agent_token'], payload)
        log_and_print("Successfully pushed daily sync to backend.")
    except Exception as e:
        log_and_print(f"Failed to push daily sync to backend: {e}", is_error=True)

def main():
    parser = argparse.ArgumentParser(description="Tally Sync Agent")
    parser.add_argument('--import-masters', action='store_true', help='Run one-time import of all debtors')
    parser.add_argument('--sync', action='store_true', help='Run daily sync of Day Book')
    
    args = parser.parse_args()
    
    if not args.import_masters and not args.sync:
        print("Please specify --import-masters or --sync")
        sys.exit(1)
        
    config = load_config()
    log_and_print(f"Connecting to Tally at {config['tally_host']}:{config['tally_port']}")
    log_and_print(f"Backend URL: {config['backend_url']}")
    
    if args.import_masters:
        asyncio.run(run_import(config))
    if args.sync:
        asyncio.run(run_sync(config))

if __name__ == "__main__":
    main()
