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
from config import load_config, save_config, company_entries
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

async def post_to_tally(host: str, port: int, payload: str, timeout: float = 180.0) -> bytes:
    url = f"http://{host}:{port}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, data=payload, headers={"Content-Type": "text/xml"})
        resp.raise_for_status()
        return resp.content

async def fetch_and_parse(config: dict, query: str, timeout: float = 180.0) -> str:
    raw = await post_to_tally(config['tally_host'], config['tally_port'], query, timeout)
    return tally_xml.sanitize_xml(raw)

async def check_pending_refresh(config: dict) -> bool:
    """Did the owner press 'Reload data' on the dashboard? If so we refresh
    outstanding immediately instead of waiting for the auto cycle."""
    try:
        base = config['backend_url'].rstrip('/')
        params = {"business_id": config['business_id'], "agent_token": config['agent_token']}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base}/tally/pending-refresh", params=params)
            resp.raise_for_status()
            return bool(resp.json().get("requested"))
    except Exception:
        return False


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
    """Live LOCAL mode, TALLY-FRIENDLY.

    Tally's HTTP server is single-threaded and wedges if polled too often, so we
    do NOT poll it on a fast timer. Instead we WATCH the PDF pickup folder (a
    cheap directory listing, zero Tally load) every few seconds. The moment a new
    bill PDF appears - i.e. the moment 'Send to ASVA' is pressed in Tally - we do
    ONE Tally read for that bill and send it to the party. So Tally is touched
    only (a) when a bill is actually sent to ASVA, and (b) on a slow background
    beat that keeps payments + the 'connected' status fresh.
    """
    # Cheap folder check (seconds) - this NEVER touches Tally, so it can be fast.
    folder_poll = max(3, int(config.get('folder_poll_seconds', 8) or 8))
    # Slow background Tally read (payments + heartbeat). Kept LONG on purpose so
    # Tally is never hammered; the folder watch above gives instant bill sending.
    sync_every = max(60, int(config.get('watch_interval_seconds', 300) or 300))
    outstanding_every = int(config.get('outstanding_every_seconds', 300) or 300)
    company = config['company_name']
    # Make sure the Tally PDF pickup folder exists (the TDL exports bills here).
    pdf_dir = config.get('bill_pdf_dir', '')
    if pdf_dir:
        try:
            os.makedirs(pdf_dir, exist_ok=True)
        except Exception as e:
            log_and_print(f"Could not create bill_pdf_dir {pdf_dir}: {e}", is_error=True)

    def _pending_pdfs() -> set:
        """PDFs waiting in the pickup folder (the /sent subfolder is a directory,
        so it is skipped). Pure filesystem - no Tally involved."""
        if not pdf_dir or not os.path.isdir(pdf_dir):
            return set()
        try:
            return {f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf')}
        except Exception:
            return set()

    log_and_print(f"WATCH MODE (local, Tally-friendly): folder checked every {folder_poll}s "
                  f"-> send the instant a bill PDF appears; background Tally read every "
                  f"{sync_every}s. Ctrl+C to stop.")

    seen = _pending_pdfs()
    last_sync = 0.0          # monotonic time of the last Tally read (0 = never)
    last_outstanding = 0.0
    failures = 0
    while True:
        stamp = datetime.now().strftime('%H:%M:%S')
        now_mono = asyncio.get_event_loop().time()

        current = _pending_pdfs()
        new_pdf = bool(current - seen)   # a bill was just sent to ASVA
        due_sync = (last_sync == 0.0) or (now_mono - last_sync >= sync_every)

        # Read Tally + deliver ONLY on a new PDF (button press) or the slow beat.
        # Never on the fast folder tick, so Tally is never hammered.
        if new_pdf or due_sync:
            try:
                if new_pdf:
                    log_and_print(f"[{stamp}] New bill PDF detected - sending to the party now.")
                new_bills, payments = await _deliver_new_bills(config, company, pdf_dir, stamp)
                if due_sync and not new_pdf:
                    log_and_print(f"[{stamp}] Tally checked - {new_bills} new bill(s) sent, {payments} payment(s).")
                last_sync = asyncio.get_event_loop().time()
                failures = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:
                failures += 1
                log_and_print(f"[{stamp}] Send/sync failed ({e}).", is_error=True)
                if failures in (5, 50):
                    log_and_print("Tally or backend unreachable for a while - check they are running.", is_error=True)
            seen = _pending_pdfs()   # delivered PDFs have moved to /sent

        # Heavy bill-wise outstanding refresh on its own slow cadence or on Reload.
        try:
            due_out = (last_outstanding == 0.0) or (now_mono - last_outstanding >= outstanding_every)
            forced = False if due_out else await check_pending_refresh(config)
            if due_out or forced:
                if forced:
                    log_and_print(f"[{stamp}] Reload pressed - refreshing outstanding now.")
                await run_apply_outstanding(config)
                last_outstanding = asyncio.get_event_loop().time()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log_and_print(f"[{stamp}] Outstanding refresh failed ({e}) - bills/heartbeat unaffected.", is_error=True)

        await asyncio.sleep(folder_poll)


async def _deliver_new_bills(config: dict, company: str, pdf_dir: str, stamp: str) -> tuple[int, int]:
    """One light watch tick: read the last 3 days of vouchers from Tally (SHORT
    timeout), attach any exported PDFs, and POST to /tally/sync. Posts on EVERY
    tick even with zero vouchers, because the backend stamps the 'Tally
    connected' heartbeat on every /sync call - so the dashboard status reflects
    reality as long as this loop is alive. Returns (new_bills, payments)."""
    today = date.today()
    frm = (today - timedelta(days=2)).strftime('%Y%m%d')
    # Short timeout (45s): the light check must never hang the tick. The heavy
    # refresh keeps the full 180s.
    xml = await fetch_and_parse(
        config, tally_xml.build_voucher_register_query(company, frm, today.strftime('%Y%m%d')),
        timeout=45.0)
    r = tally_xml.parse_vouchers(xml)
    vouchers = _vouchers_payload(r['sales'], r['receipts'])
    _attach_tally_pdfs(vouchers, pdf_dir)
    pdf_srcs = {}
    for v in vouchers:
        src = v.pop('_pdf_src', None)   # keep it OUT of the wire payload
        if src:
            pdf_srcs[v.get('voucher_number')] = src

    # ALWAYS post (even 0 vouchers) so the heartbeat stays fresh every tick.
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
    # Move sent bills' PDFs into <folder>/sent so the pickup folder stays clean.
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
    return new_bills, payments


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

    # Ledger ClosingBalance per party = Tally's authoritative "owes today" total.
    # The backend uses this as the source of truth for the amount and only keeps
    # the bill-wise breakdown when it reconciles. Parties whose ledgers don't
    # 'maintain balances bill-by-bill' (zero bills) are still correct via this.
    ledger_balances = {
        d['name']: round(float(d.get('current_outstanding') or 0), 2)
        for d in debtors if (d.get('current_outstanding') or 0) > 0
    }

    bills_xml = await fetch_and_parse(config, tally_xml.build_bills_query(company))
    bills = tally_xml.parse_bills(bills_xml, set(debtor_names))
    if not bills and not ledger_balances:
        log_and_print("No outstanding data from Tally - nothing refreshed.", is_error=True)
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
        "ledger_balances": ledger_balances,
    }
    log_and_print(f"Refreshing outstanding from Tally: {len(bills)} bills, "
                  f"{len(ledger_balances)} parties owing (ledger totals authoritative)...")
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

async def run_watch_all(companies: list):
    """Watch every connected company. One company = the original run_watch
    (zero behaviour change). Several = one shared cycle that checks each
    company IN TURN - Tally's single-threaded HTTP server must never see
    parallel requests."""
    if len(companies) == 1:
        await run_watch(companies[0])
        return

    interval = int(companies[0].get('watch_interval_seconds', 60) or 60)
    outstanding_every = int(companies[0].get('outstanding_every_seconds', 300) or 300)
    last_out = {c['company_name']: 0.0 for c in companies}
    pdf_dir = companies[0].get('bill_pdf_dir', '')
    if pdf_dir:
        try:
            os.makedirs(pdf_dir, exist_ok=True)
        except Exception as e:
            log_and_print(f"Could not create bill_pdf_dir {pdf_dir}: {e}", is_error=True)
    names = ", ".join(c['company_name'] for c in companies)
    log_and_print(f"WATCH MODE ({len(companies)} companies: {names}) - tick every {interval}s.")

    while True:
        for cfg in companies:
            company = cfg['company_name']
            stamp = datetime.now().strftime('%H:%M:%S')
            # STEP 1 (fast): deliver new bills + heartbeat, isolated per company.
            try:
                nb, pm = await _deliver_new_bills(cfg, company, pdf_dir, stamp)
                log_and_print(f"[{stamp}] {company}: checked - {nb} new bill(s), {pm} payment(s).")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log_and_print(f"[{stamp}] {company}: bill check failed ({e}) - next company.",
                              is_error=True)
            # STEP 2 (on cadence, isolated): heavy outstanding refresh.
            try:
                now_mono = asyncio.get_event_loop().time()
                due = (last_out[company] == 0.0
                       or now_mono - last_out[company] >= outstanding_every)
                forced = False if due else await check_pending_refresh(cfg)
                if due or forced:
                    if forced:
                        log_and_print(f"[{stamp}] {company}: Reload pressed - refreshing now.")
                    await run_apply_outstanding(cfg)
                    last_out[company] = asyncio.get_event_loop().time()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log_and_print(f"[{stamp}] {company}: outstanding refresh failed ({e}).",
                              is_error=True)
        await asyncio.sleep(interval)


async def run_list_companies(config: dict):
    """Print the companies currently OPEN in Tally, marking the ones this
    agent already serves. This is what the owner uses to decide what to add."""
    xml = await fetch_and_parse(config, tally_xml.build_company_list_query())
    open_companies = tally_xml.parse_companies(xml)
    served = {c['company_name'] for c in company_entries(config)}
    if not open_companies:
        log_and_print("No companies reported by Tally - is Tally open?", is_error=True)
        return
    log_and_print(f"Companies open in Tally ({len(open_companies)}):")
    for name in open_companies:
        mark = " [connected to ASVA]" if name in served else ""
        log_and_print(f"  - {name}{mark}")
    log_and_print('Add one with:  agent --add-company "EXACT NAME"')


async def run_add_company(config: dict, name: str):
    """Register another Tally company under this customer's account: the
    backend creates its own isolated business (own data, own token), and the
    credentials are saved into config.json. Idempotent."""
    name = (name or "").strip()
    if not name:
        log_and_print("Company name required: --add-company \"EXACT NAME\"", is_error=True)
        return
    served = {c['company_name'] for c in company_entries(config)}
    if name in served:
        log_and_print(f"'{name}' is already connected to ASVA.")
        return
    # Verify against Tally's open companies (typo protection = data accuracy).
    try:
        xml = await fetch_and_parse(config, tally_xml.build_company_list_query())
        open_companies = tally_xml.parse_companies(xml)
        if open_companies and name not in open_companies:
            log_and_print(f"'{name}' is not open in Tally. Open companies: {open_companies}", is_error=True)
            log_and_print("Open it in Tally (or fix the spelling) and try again.", is_error=True)
            return
    except Exception as e:
        log_and_print(f"Could not verify against Tally ({e}) - continuing.", is_error=True)

    base = config['backend_url'].rstrip('/')
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{base}/tally/companies/register", json={
            "account_token": config['agent_token'],
            "company_name": name,
        })
        resp.raise_for_status()
        data = resp.json()

    raw = load_config()
    raw.setdefault('companies', [])
    raw['companies'].append({
        "company_name": data['company_name'],
        "business_id": data['business_id'],
        "agent_token": data['agent_token'],
    })
    save_config(raw)
    log_and_print(f"Connected '{name}' to ASVA (its own separate data). Saved to config.json.")
    log_and_print("Now run:  agent --import-masters   (one-time, brings its debtors in)")


def main():
    parser = argparse.ArgumentParser(description="Tally Sync Agent")
    parser.add_argument('--import-masters', action='store_true', help='Run one-time import of all debtors')
    parser.add_argument('--sync', action='store_true', help='Run daily sync of Day Book')
    parser.add_argument('--watch', action='store_true', help='Live mode: push new bills to WhatsApp within ~2 minutes')
    parser.add_argument('--check-outstanding', action='store_true', help='PREVIEW bill-by-bill outstanding from Tally (changes nothing)')
    parser.add_argument('--refresh-outstanding', action='store_true', help='Apply Tally bill-by-bill outstanding as the source of truth (accurate amounts + dates)')
    parser.add_argument('--companies', action='store_true', help='List companies open in Tally (and which are connected to ASVA)')
    parser.add_argument('--add-company', metavar='NAME', help='Connect another Tally company to ASVA (its own separate data)')

    args = parser.parse_args()

    if not (args.import_masters or args.sync or args.watch or args.check_outstanding
            or args.refresh_outstanding or args.companies or args.add_company):
        print("Please specify --import-masters, --sync, --watch, --check-outstanding, "
              "--refresh-outstanding, --companies or --add-company \"NAME\"")
        sys.exit(1)

    config = load_config()

    # Auto-discover host and port
    host, port = asyncio.run(auto_discover_tally(config))
    config['tally_host'] = host
    config['tally_port'] = port

    log_and_print(f"Connecting to Tally at {config['tally_host']}:{config['tally_port']}")
    log_and_print(f"Backend URL: {config['backend_url']}")

    if args.companies:
        asyncio.run(run_list_companies(config))
        return
    if args.add_company:
        asyncio.run(run_add_company(config, args.add_company))
        return

    # Every data action runs for EVERY connected company, one after another
    # (Tally's HTTP server is single-threaded - never talk to it in parallel).
    companies = company_entries(config)
    if len(companies) > 1:
        log_and_print(f"Serving {len(companies)} companies: "
                      + ", ".join(c['company_name'] for c in companies))

    for cfg in companies:
        log_and_print(f"Company: {cfg['company_name']}")
        asyncio.run(check_company(cfg))
        if args.check_outstanding:
            asyncio.run(run_check_outstanding(cfg))
        if args.refresh_outstanding:
            asyncio.run(run_apply_outstanding(cfg))
        if args.import_masters:
            asyncio.run(run_import(cfg))
        if args.sync:
            asyncio.run(run_sync(cfg))

    if args.watch:
        try:
            asyncio.run(run_watch_all(companies))
        except KeyboardInterrupt:
            log_and_print("Watch stopped.")

if __name__ == "__main__":
    main()
