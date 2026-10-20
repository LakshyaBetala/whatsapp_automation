# ASVA on Google Cloud (free trial bridge)

Use this when Oracle Always Free is unavailable. The $300 / 90-day trial covers
about 2 to 3 months. Before day 90, either pay for the VM or move to another host.

## 0. Before you start (you do these yourself)

- Create the Cloud Billing account in INR if Google asks for it (India rule).
- Enter the card yourself. Never type it into chat or a script.
- Do not start the trial while the Kamatera box is serving shops, or the two
  boxes will both send WhatsApp reminders. Power Kamatera off first.

## 1. Install and log in to gcloud (laptop)

```powershell
winget install Google.CloudSDK
gcloud init            # pick the new account, the billing account, project asva-prod
gcloud config set project asva-prod
gcloud config set compute/region asia-south1
gcloud config set compute/zone asia-south1-a
```

## 2. Create the VM

Mumbai, x86, 2 vCPU / 4 GB, Ubuntu 24.04 LTS, 40 GB disk. Restricted SSH
(Tailscale carries admin access; no public ports except the tunnel).

```powershell
gcloud compute instances create asva-gcp-1 `
  --machine-type=e2-medium `
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud `
  --boot-disk-size=40GB --boot-disk-type=pd-balanced `
  --tags=asva `
  --metadata=enable-oslogin=TRUE
```

Do not open any firewall rule for 80, 443, 8000 or 3002. Cloudflare Tunnel
serves the public links.

## 3. Get in and restore

```powershell
gcloud compute ssh asva-gcp-1
```

On the VM, copy the state bundle and passphrase from the laptop
(`~/Documents/ASVA-state/`), then run the bootstrap exactly as in
`deploy/VPS_MIGRATION.md` (the "bundle" path, 9 steps). The bootstrap
creates swap, the `server-asva` user, cloudflared with the token, and the
bot env. Restore the bot session from the bundle so WhatsApp does not need
a new QR scan.

## 4. Verify before you trust it

- `https://app.tryasva.com/health` returns 200.
- `https://link.tryasva.com/health` returns 200.
- Bot connected (`wa_service` logs show open, not QR).
- A test reminder reaches the owner's number.
- `pg_dump` backup runs and verifies.
- Scheduler jobs run once (check the logs), then leave them.

## 5. Keep it free and safe

- Set a budget alert at $50 in Billing, so a surprise charge is caught early.
- Calendar reminder for day 75: pay for the VM, or migrate.
- Stop the VM if you need to pause: `gcloud compute instances stop asva-gcp-1`.
  The disk still costs a little, but the VM does not run and cannot send.

## Costs (check before paying)

`e2-medium` in asia-south1 is about $0.066/hour on demand (third-party figure,
not verified on Google's price list). Run the Pricing Calculator for the exact
INR amount before upgrading past the trial.
