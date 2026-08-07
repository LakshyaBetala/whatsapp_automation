"""Fake-Tally seeder for the ISOLATED LOCAL dev database.

Creates a dev business + a dozen debtors, then POSTs them through the REAL
/tally/import and /tally/sync endpoints - the same contract the shipped agent
uses - so the whole dashboard (outstanding, overdue, credit days, reminders,
per-party pages, payments) is populated WITHOUT Tally installed.

Run it via  bash dev/run_dev.sh seed   (which loads dev/.env.dev first).
Re-running is safe: it reuses the same dev business (matched by agent_token).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date, timedelta

from supabase import create_client

BACKEND = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
SUPA_URL = os.environ["SUPABASE_URL"]
SUPA_KEY = os.environ["SUPABASE_SERVICE_KEY"]
AGENT_TOKEN = os.environ.get("TALLY_AGENT_TOKEN", "dev-agent-token")

db = create_client(SUPA_URL, SUPA_KEY)


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BACKEND}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def ensure_business() -> str:
    """Reuse the dev business if present (by agent_token), else create it."""
    found = db.table("businesses").select("id").eq("agent_token", AGENT_TOKEN).execute()
    if found.data:
        return found.data[0]["id"]
    row = db.table("businesses").insert({
        "owner_name": "Dev Owner",
        "business_name": "DEV Wholesale (test data)",
        "whatsapp_number": "919000000001",   # fake; never messaged (WA is a no-op in dev)
        "plan": "starter",
        "agent_token": AGENT_TOKEN,
        "reminders_enabled": True,
    }).execute()
    return row.data[0]["id"]


# 12 debtors: a spread of balances, credit terms, and phone-on-file so the
# dashboard shows real variety (some remindable, some not).
DEBTORS = [
    ("Sri Balaji Traders",     185000, "919812300001", 30),
    ("Anand Electricals",       92500, "919812300002", 45),
    ("Kumar & Sons",           310000, "919812300003", 60),
    ("Lakshmi Enterprises",     47800, "919812300004", 30),
    ("Venkatesh Hardware",     128000, None,           30),   # no phone -> not remindable
    ("Ganesh Distributors",    256000, "919812300006", 90),
    ("Ravi Trading Co",         64200, "919812300007", 30),
    ("Sai Krishna Agencies",   142000, "919812300008", 45),
    ("New India Stores",        38900, "919812300009", 30),
    ("Meenakshi Traders",      201500, "919812300010", 60),
    ("Deepak Electric House",   55000, None,           30),
    ("Priya Wholesale",        173000, "919812300012", 30),
]


def seed() -> None:
    biz_id = ensure_business()
    print(f"Dev business: {biz_id}")

    debtors = [{
        "name": n,
        "opening_balance": float(bal),
        "tally_group": "Sundry Debtors",
        "whatsapp_number": ph,
        "credit_days": cd,
        "tally_guid": f"dev-guid-{i:03d}",
    } for i, (n, bal, ph, cd) in enumerate(DEBTORS, 1)]

    r = _post("/tally/import", {
        "business_id": biz_id,
        "agent_token": AGENT_TOKEN,
        "company_name": "DEV Wholesale",
        "debtors": debtors,
    })
    print(f"/tally/import -> {r}")

    # Sales vouchers spread over the last ~70 days so several are overdue, plus
    # two receipts to exercise FIFO allocation and the Payments tab.
    today = date.today()
    def d(days_ago): return (today - timedelta(days=days_ago)).isoformat()
    vouchers = [
        {"voucher_number": "S-1001", "party_name": "Sri Balaji Traders", "date": d(65), "amount": 60000, "voucher_type": "Sales"},
        {"voucher_number": "S-1002", "party_name": "Anand Electricals",  "date": d(50), "amount": 40000, "voucher_type": "Sales"},
        {"voucher_number": "S-1003", "party_name": "Kumar & Sons",       "date": d(40), "amount": 90000, "voucher_type": "Sales"},
        {"voucher_number": "S-1004", "party_name": "Lakshmi Enterprises","date": d(20), "amount": 25000, "voucher_type": "Sales"},
        {"voucher_number": "S-1005", "party_name": "Ganesh Distributors","date": d(10), "amount": 80000, "voucher_type": "Sales"},
        {"voucher_number": "S-1006", "party_name": "Ravi Trading Co",    "date": d(5),  "amount": 30000, "voucher_type": "Sales"},
        {"voucher_number": "R-2001", "party_name": "Sri Balaji Traders", "date": d(3),  "amount": 60000, "voucher_type": "Receipt"},
        {"voucher_number": "R-2002", "party_name": "Anand Electricals",  "date": d(1),  "amount": 15000, "voucher_type": "Receipt"},
    ]
    r = _post("/tally/sync", {
        "business_id": biz_id,
        "agent_token": AGENT_TOKEN,
        "company_name": "DEV Wholesale",
        "sync_date": today.isoformat(),
        "vouchers": vouchers,
    })
    print(f"/tally/sync -> {r}")

    # Populate the Payments tab via the REAL detection path: two customers
    # "reply PAID" to their shop, so the tab shows "customers who say they paid"
    # + a receipt "ready to post to Tally". This is the exact code a live reply
    # runs - nothing faked server-side.
    import time as _t
    for num, msg, mid in [
        ("919812300007", "PAID 30000", f"dev-pay-{int(_t.time())}-1"),   # Ravi Trading Co
        ("919812300010", "paid 50000", f"dev-pay-{int(_t.time())}-2"),   # Meenakshi Traders
    ]:
        try:
            _post("/webhooks/aisensy",
                  {"data": {"sender": num, "message": msg, "channel": "shop", "messageId": mid}})
        except Exception as e:
            print("  payment-detection sim skipped:", e)
    print("Payments tab seeded (2 customers reported a payment).")

    print("\nSeed done. Open the dashboard:")
    print(f"  Owner view : {BACKEND}/admin?token={AGENT_TOKEN}")
    print(f"  Command Ctr: {BACKEND}/ops?key={os.environ.get('ADMIN_API_KEY','devkey')}")


if __name__ == "__main__":
    seed()
