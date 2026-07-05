"""Admin tool: extend a business's subscription after they pay.

Runs on the hosting laptop (needs .env with the Supabase service key):

    python renew.py <business_id> [--days 30]
    python renew.py --list           # show all businesses + status
"""
import argparse
import sys
from datetime import date, timedelta

from app.db import require_db
from app.services import subscription as subs


def main() -> None:
    ap = argparse.ArgumentParser(description="Renew a business subscription")
    ap.add_argument('business_id', nargs='?', help="Business UUID")
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--list', action='store_true', help="List businesses with subscription status")
    args = ap.parse_args()

    db = require_db()

    if args.list or not args.business_id:
        rows = db.table("businesses").select(
            "id, business_name, plan, plan_expires_on, subscription_status").execute()
        for b in rows.data or []:
            live = subs.effective_status(b.get("plan_expires_on"))
            print(f"{b['id']}  {b.get('business_name') or '—':32.32}  "
                  f"expires {b.get('plan_expires_on') or '—'}  [{live}]")
        return

    row = db.table("businesses").select("business_name, plan_expires_on").eq(
        "id", args.business_id).single().execute()
    if not row.data:
        sys.exit(f"No business with id {args.business_id}")

    current = row.data.get("plan_expires_on")
    base = date.fromisoformat(str(current)) if current else date.today()
    new_expiry = max(base, date.today()) + timedelta(days=args.days)

    db.table("businesses").update({
        "plan_expires_on": new_expiry.isoformat(),
        "subscription_status": "active",
    }).eq("id", args.business_id).execute()

    print(f"✅ {row.data.get('business_name')}: renewed {args.days} days -> expires {new_expiry}")
    print("Sends resume immediately (the gate reads plan_expires_on live).")


if __name__ == '__main__':
    main()
