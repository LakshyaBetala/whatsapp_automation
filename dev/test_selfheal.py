"""Integration proof of the receipt self-heal, run against the LOCAL dev DB.
   bash dev/run_dev.sh   (in one shell, to have the stack up)  then:
   .venv/.../python dev/test_selfheal.py   with dev/.env.dev loaded.
"""
import datetime as dt
import os
import sys

from supabase import create_client

sys.path.insert(0, os.getcwd())
from app.services import receipts_queue as rq  # noqa: E402

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# a throwaway business for this test
BIZ = None
found = db.table("businesses").select("id").eq("agent_token", "selfheal-test").execute().data
if found:
    BIZ = found[0]["id"]
else:
    BIZ = db.table("businesses").insert({
        "owner_name": "SH", "business_name": "SelfHeal Test",
        "whatsapp_number": "919000009999", "plan": "starter",
        "agent_token": "selfheal-test", "reminders_enabled": True,
    }).execute().data[0]["id"]

# clean slate
db.table("pending_receipts").delete().eq("business_id", BIZ).execute()

def status_of(pid):
    return db.table("pending_receipts").select("status,posting_at").eq("id", pid).execute().data[0]

# 1. create + confirm
p = rq.create_pending(db, BIZ, client_id=None, party_ledger="Test Party",
                      party_display="Test Party", amount=5000)
pid = p["id"]
rq.confirm(db, BIZ, pid)
assert status_of(pid)["status"] == "confirmed", "should be confirmed"
print("1. created + confirmed  OK")

# 2. agent claims -> posting, posting_at stamped
claimed = rq.claim_confirmed(db, BIZ)
assert any(r["id"] == pid for r in claimed), "claim should return it"
st = status_of(pid)
assert st["status"] == "posting" and st["posting_at"], "should be posting with posting_at"
print("2. claimed -> posting (posting_at set)  OK")

# 3. lost report: it stays posting. A second claim within the window must NOT
#    re-hand it (no double post).
again = rq.claim_confirmed(db, BIZ)
assert not any(r["id"] == pid for r in again), "fresh posting must NOT be re-claimed"
print("3. fresh 'posting' not re-handed (no double post)  OK")

# 4. backdate posting_at to 20 min ago -> simulate a genuinely lost report
old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).isoformat()
db.table("pending_receipts").update({"posting_at": old}).eq("id", pid).execute()

# 5. next claim self-heals: stale posting -> reclaimed -> handed out again
healed = rq.claim_confirmed(db, BIZ)
assert any(r["id"] == pid for r in healed), "stale posting must self-heal and be re-claimed"
print("4. stale 'posting' self-healed + re-claimed  OK")

# 6. agent finally reports success -> posted, leaves the owner's tab
rq.mark(db, pid, "posted", voucher_id="V-1", allocation=[{"ref": "B1", "amount": 5000}])
assert status_of(pid)["status"] == "posted"
assert not any(r["id"] == pid for r in rq.list_for_owner(db, BIZ)), "posted leaves the tab"
print("5. reported posted -> cleared from tab  OK")

# cleanup
db.table("pending_receipts").delete().eq("business_id", BIZ).execute()
print("\nALL SELF-HEAL CHECKS PASSED")
