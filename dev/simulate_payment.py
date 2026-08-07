"""Simulate a customer reporting a payment on WhatsApp - for a live pitch demo.

It POSTs to the SAME inbound webhook a real WhatsApp reply hits, so the app's
real payment-detection runs: the customer is matched by number, a receipt lands
in the Payments tab, and reminders pause. Nothing is faked server-side.

Usage (number is EDITABLE - use any seeded customer's number, or add your own):
    python dev/simulate_payment.py <number> <amount> ["message"]
Examples:
    python dev/simulate_payment.py 919812300003 90000
    python dev/simulate_payment.py 919812300001 25000 "paid 25000 by gpay"
"""
import json
import os
import sys
import time
import urllib.request

BACKEND = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    number = sys.argv[1].strip()
    amount = sys.argv[2].strip()
    message = sys.argv[3] if len(sys.argv) > 3 else f"PAID {amount}"
    body = {"data": {"sender": number, "message": message, "channel": "shop",
                     "messageId": f"sim-{int(time.time())}"}}
    req = urllib.request.Request(f"{BACKEND}/webhooks/aisensy",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        print("webhook ->", r.read().decode())
    print(f"\nSent '{message}' from {number}. Open the Payments tab - it should now show "
          f"this customer under 'says they paid' with a receipt ready to post to Tally.")


if __name__ == "__main__":
    main()
