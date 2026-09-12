# busy_agent

ASVA's reader for **BUSY Accounting Software** (busy.in), the second most used
accounting package for Indian SMBs, strongest in the north where Tally is
weaker. Same desktop app, same interface, same features; only the reader behind
"Get latest" changes.

## Status: discovery

BUSY's own FAQ states it "does not have any default option for integration of
any third party software"; its API is channel-partner only with no public spec.
There is no Tally-style always-on local server. What BUSY does have is a known
database: **MS Access (.mdb/.bds)** in the file-server edition, **MS SQL Server**
in the client-server edition. Both are readable over ODBC, read-only, while BUSY
is open. That is the read path.

We need a real install to learn the schema. On any Windows machine with BUSY:

```
pip install pyodbc
python busy_agent\discover.py
```

and send back `busy_discovery.json`. It is read-only and contains structure
and counts only, never transaction data. If it reports "no Microsoft Access
ODBC driver", install the free *Microsoft Access Database Engine 2016
Redistributable* (match BUSY's bitness) and run it again.

## Contract

`source_contract.py` is the spec: every accounting reader produces `Party`,
`OpenBill`, `Voucher` and an `OutstandingSnapshot`, exactly as the backend
already consumes them from Tally. Read side (`read_outstanding`,
`read_vouchers`) is expected to be straightforward once the schema is known.
`post_receipt` (writing a confirmed payment back) is the open question, exactly
as it was for Tally before `build_receipt_import` was proven live; the
candidate path is BUSY's XML voucher import.
