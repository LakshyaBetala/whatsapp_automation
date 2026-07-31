# ASVA SHOP AGENT (thin client) - setup

This laptop only reads Tally and sends it to the ASVA host. No backend, no
WhatsApp, no database run here. Reminders, the digest, sending, and the
Command Center all run on the host. This keeps this laptop light and means
reminders still go out on days this laptop is off.

## One-time setup

1. Unzip `ASVA_shop_client.zip` to `C:\ASVA`.
2. Get your config from the operator: in the host Command Center they click
   **+ Add business** and it produces a `config.json` for you.
3. Open `C:\ASVA\tally_agent\config.json` and paste the three values:
   - `backend_url` - the host's URL, e.g. `https://asva.YOURDOMAIN.com`
   - `business_id` - from Add Business
   - `agent_token` - from Add Business (keep this secret)
   - `company_name` - your exact Tally company name (as shown in Tally).
4. Make sure TallyPrime is open with that company, and that Tally's
   "Act as Server" / ODBC over port 9000 is on (Gateway of Tally -> F1 Help ->
   Settings -> Connectivity, or it is usually on by default).

## Every day

- Keep Tally open.
- Double-click **`AGENT_ONLY.bat`**. It reads new bills + payments and pushes
  them to the host every couple of minutes, and restarts itself if it stops.

## Autostart (recommended)

1. Press `Win + R`, type `shell:startup`, Enter.
2. Put a shortcut to `AGENT_ONLY.bat` in that folder.

Now it starts with Windows.

## Is it working?

- The `AGENT_ONLY.bat` window shows it reaching Tally and the host.
- In the host Command Center this shop shows **online** within a minute.
- New sales bills go to customers over WhatsApp from the shop's own number
  (that number is linked once on the host).
