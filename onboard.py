"""One-command SMB onboarding — registers the business and builds the
ready-to-ship agent folder. This is the '30-minute setup' path:

    python onboard.py --owner "Rishab" --business "RISHAB TRADING COMPANY" \\
        --phone 9198xxxxxxxx --tally-company "RISHAB TRADING COMPANY" \\
        --backend http://192.168.1.50:8000

Output: agents/<slug>/ containing config.json (+ the agent .exe if a
build exists in TallyAgentRelease/). Copy that folder to the customer's
Tally PC, double-click the exe with --import-masters, done.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'business'


def main() -> None:
    ap = argparse.ArgumentParser(description="Onboard a new SMB")
    ap.add_argument('--owner', required=True, help="Owner's name")
    ap.add_argument('--business', required=True, help="Business name")
    ap.add_argument('--phone', required=True, help="Owner WhatsApp (10 digits or 91XXXXXXXXXX)")
    ap.add_argument('--tally-company', required=True, help="EXACT company name as shown in Tally")
    ap.add_argument('--backend', default='http://localhost:8000', help="Backend base URL")
    ap.add_argument('--plan', default='starter', choices=['starter', 'growth', 'pro', 'max'])
    ap.add_argument('--tally-host', default='localhost', help="Where Tally runs, from the agent's point of view")
    ap.add_argument('--credit-days', type=int, default=None, help="Shop-wide default credit period (days)")
    args = ap.parse_args()

    # 1. Health check first — fail fast with a clear message
    try:
        health = httpx.get(f"{args.backend.rstrip('/')}/health", timeout=10).json()
    except Exception as e:
        sys.exit(f"ERROR: backend not reachable at {args.backend} — start it first ({e})")
    if not health.get('supabase_configured'):
        sys.exit("ERROR: backend is up but Supabase is not configured (.env missing keys).")

    # 2. Register the business
    resp = httpx.post(f"{args.backend.rstrip('/')}/businesses/register", json={
        "owner_name": args.owner,
        "business_name": args.business,
        "whatsapp_number": args.phone,
        "plan": args.plan,
        "tally_company_name": args.tally_company,
    }, timeout=30)
    if resp.status_code == 409:
        sys.exit("ERROR: a business with this WhatsApp number is already registered.")
    resp.raise_for_status()
    biz = resp.json()

    # 3. Build the agent folder
    out_dir = ROOT / 'agents' / slugify(args.business)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "business_id": biz["id"],
        "agent_token": biz["agent_token"],
        "tally_host": args.tally_host,
        "tally_port": 9000,
        "backend_url": args.backend,
        "company_name": args.tally_company,
    }
    if args.credit_days:
        config["default_credit_days"] = args.credit_days
    (out_dir / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')

    exe = ROOT / 'Asva' / 'Asva.exe'
    if exe.exists():
        shutil.copy2(exe, out_dir / 'Asva.exe')

    print(f"\n✅ {args.business} registered (plan: {args.plan})")
    print(f"   business_id: {biz['id']}")
    print(f"   Agent folder: {out_dir}")
    print("\nNext steps:")
    print(f"  1. Copy {out_dir.name}/ to the customer's Tally PC")
    print("  2. In Tally: F1 > Settings > Connectivity > act as server, port 9000")
    print("  3. Run: Asva.exe --import-masters")
    print("  4. Schedule daily: Asva.exe --sync  (Task Scheduler, ~8 PM)")


if __name__ == '__main__':
    main()
