"""ASVA DSO baseline - the "before" number, computed from Tally exports.

Run this ONCE on the shop laptop against 12 months of history to establish the
collections baseline BEFORE ASVA goes live. It is stdlib-only and never touches
the network, the database, or Tally directly - it reads two CSV files you export
from Tally, does FIFO payment allocation per customer, and prints the metrics.

    Export from Tally (Gateway -> Display -> Account Books):
      1) Sales Register  -> last 12 months -> Export as CSV  (sales.csv)
      2) The receipt side: either the party ledgers, or Day Book filtered to
         Receipt vouchers -> Export as CSV                    (receipts.csv)
    You only need three columns in each: a DATE, the CUSTOMER/PARTY name, and an
    AMOUNT. An invoice/voucher number column is used if present.

    python dso_baseline.py sales.csv receipts.csv
    python dso_baseline.py --selftest        # verify the math with synthetic data

Outputs: a printed report + dso_per_invoice.csv + dso_by_customer.csv.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

# ── parsing helpers ─────────────────────────────────────────────────────────
_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                 "%d.%m.%Y", "%d %b %Y", "%m/%d/%Y")


def parse_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(raw: str) -> float:
    s = (raw or "").strip().replace(",", "").replace("₹", "").replace("Rs", "").replace("INR", "")
    s = s.replace("Dr", "").replace("Cr", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not s:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def norm_party(raw: str) -> str:
    return " ".join((raw or "").strip().upper().split())


def _find(headers: list[str], *keywords: str) -> str | None:
    low = {h: h.lower() for h in headers}
    for kw in keywords:
        for h in headers:
            if kw in low[h]:
                return h
    return None


def read_rows(path: str, kind: str) -> list[dict]:
    """Read a Tally CSV; auto-map date/party/amount(/voucher) columns."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        # Tally CSVs sometimes have preamble lines; find the header row.
        sample = f.read()
    lines = sample.splitlines()
    # Pick the first line that looks like a header (has 'date' and a name-ish col).
    start = 0
    for i, ln in enumerate(lines[:15]):
        low = ln.lower()
        if "date" in low and ("particular" in low or "party" in low or "name" in low
                              or "ledger" in low or "customer" in low):
            start = i
            break
    reader = csv.DictReader(lines[start:])
    headers = [h for h in (reader.fieldnames or []) if h]
    c_date = _find(headers, "date")
    c_party = _find(headers, "particular", "party", "customer", "ledger", "name", "account")
    c_amt = _find(headers, "amount", "gross", "value", "debit", "credit", "total")
    c_vch = _find(headers, "vch no", "voucher no", "invoice", "bill no", "vch", "ref")
    if not (c_date and c_party and c_amt):
        raise SystemExit(
            f"[{path}] could not find date/party/amount columns.\n"
            f"  detected headers: {headers}\n"
            f"  need one each of: a DATE, a PARTY/PARTICULARS, an AMOUNT column.")
    out = []
    for r in reader:
        d = parse_date(r.get(c_date, ""))
        p = norm_party(r.get(c_party, ""))
        a = abs(parse_amount(r.get(c_amt, "")))
        if not d or not p or a <= 0:
            continue
        out.append({"date": d, "party": p, "amount": a,
                    "vch": (r.get(c_vch, "") or "").strip() if c_vch else ""})
    if not out:
        raise SystemExit(f"[{path}] parsed 0 usable rows - check the exported columns.")
    print(f"  {kind}: {len(out)} rows  (columns: date='{c_date}', party='{c_party}', amount='{c_amt}')")
    return out


# ── core model ──────────────────────────────────────────────────────────────
@dataclass
class Invoice:
    party: str
    date: date
    amount: float
    vch: str
    received: float = 0.0
    closed_on: date | None = None
    pays: list = field(default_factory=list)   # (date, amount)

    @property
    def outstanding(self) -> float:
        return round(self.amount - self.received, 2)

    @property
    def closed(self) -> bool:
        return self.received >= self.amount - 0.01

    def collect_days(self) -> int | None:
        if not self.closed or self.closed_on is None:
            return None
        return max(0, (self.closed_on - self.date).days)


def allocate_fifo(sales: list[dict], receipts: list[dict]) -> list[Invoice]:
    """Apply each customer's receipts to their oldest open invoices first,
    exactly like the backend's /tally/sync. A receipt splits across bills; an
    invoice's closed date is the date of the payment that finishes it."""
    by_party_inv: dict[str, list[Invoice]] = defaultdict(list)
    for s in sales:
        by_party_inv[s["party"]].append(Invoice(s["party"], s["date"], s["amount"], s["vch"]))
    for lst in by_party_inv.values():
        lst.sort(key=lambda i: (i.date, i.vch))

    rec_by_party: dict[str, list[dict]] = defaultdict(list)
    for r in receipts:
        rec_by_party[r["party"]].append(r)
    for lst in rec_by_party.values():
        lst.sort(key=lambda r: r["date"])

    for party, recs in rec_by_party.items():
        invs = by_party_inv.get(party, [])
        for r in recs:
            money = r["amount"]
            for inv in invs:
                if money <= 0:
                    break
                if inv.closed:
                    continue
                take = min(money, inv.outstanding)
                if take <= 0:
                    continue
                inv.received = round(inv.received + take, 2)
                inv.pays.append((r["date"], take))
                money -= take
                if inv.closed:
                    inv.closed_on = r["date"]
            # leftover money (advance / on-account) is ignored for DSO
    return [inv for lst in by_party_inv.values() for inv in lst]


# ── metrics + report ────────────────────────────────────────────────────────
def _bucket(days: int) -> str:
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def report(invoices: list[Invoice], today: date) -> dict:
    closed = [i for i in invoices if i.closed]
    open_ = [i for i in invoices if not i.closed]
    total_amt = sum(i.amount for i in invoices)

    days = [i.collect_days() for i in closed]
    w_num = sum(i.amount * i.collect_days() for i in closed)
    w_den = sum(i.amount for i in closed) or 1
    weighted_avg = w_num / w_den
    simple_avg = statistics.mean(days) if days else 0
    median = statistics.median(days) if days else 0

    # collection curve: of ALL invoices, share fully paid within N days
    n = len(invoices) or 1
    amt = total_amt or 1
    curve = {}
    for label, cap in (("<=30", 30), ("<=60", 60), ("<=90", 90)):
        cnt = sum(1 for i in closed if i.collect_days() <= cap)
        camt = sum(i.amount for i in closed if i.collect_days() <= cap)
        curve[label] = (100 * cnt / n, 100 * camt / amt)
    beyond_cnt = n - sum(1 for i in closed if i.collect_days() <= 90)
    beyond_amt = amt - sum(i.amount for i in closed if i.collect_days() <= 90)
    curve[">90/open"] = (100 * beyond_cnt / n, 100 * beyond_amt / amt)

    aging: dict[str, float] = defaultdict(float)
    for i in open_:
        aging[_bucket((today - i.date).days)] += i.outstanding

    out_by_party: dict[str, float] = defaultdict(float)
    for i in open_:
        out_by_party[i.party] += i.outstanding
    top = sorted(out_by_party.items(), key=lambda kv: -kv[1])[:20]

    per_cust = {}
    cust_invs: dict[str, list[Invoice]] = defaultdict(list)
    for i in invoices:
        cust_invs[i.party].append(i)
    for p, lst in cust_invs.items():
        cd = [i.collect_days() for i in lst if i.closed]
        per_cust[p] = {
            "avg_days": round(statistics.mean(cd), 1) if cd else None,
            "closed": len(cd), "open": sum(1 for i in lst if not i.closed),
            "outstanding": round(sum(i.outstanding for i in lst if not i.closed), 2),
        }

    return dict(total_invoices=len(invoices), closed=len(closed), open=len(open_),
                total_amount=total_amt, outstanding=sum(i.outstanding for i in open_),
                weighted_avg_days=weighted_avg, simple_avg_days=simple_avg,
                median_days=median, curve=curve, aging=aging, top=top, per_cust=per_cust)


def print_report(rep: dict) -> None:
    inr = lambda v: f"Rs {v:,.0f}"
    print("\n" + "=" * 60)
    print("  ASVA - COLLECTIONS BASELINE (before ASVA)")
    print("=" * 60)
    print(f"  Invoices analysed : {rep['total_invoices']:,}  "
          f"(paid {rep['closed']:,} / open {rep['open']:,})")
    print(f"  Total invoiced    : {inr(rep['total_amount'])}")
    print(f"  Still outstanding : {inr(rep['outstanding'])}")
    print("\n  DAYS TO COLLECT (the headline number)")
    print(f"    Amount-weighted average : {rep['weighted_avg_days']:.0f} days   <- your DSO")
    print(f"    Simple average          : {rep['simple_avg_days']:.0f} days")
    print(f"    Median                  : {rep['median_days']:.0f} days")
    print("\n  COLLECTION CURVE (share fully paid within N days)")
    for k, (c, a) in rep["curve"].items():
        print(f"    {k:>9} : {c:5.1f}% of invoices   {a:5.1f}% of value")
    print("\n  OUTSTANDING AGEING")
    for b in ("0-30", "31-60", "61-90", "90+"):
        print(f"    {b:>6} days : {inr(rep['aging'].get(b, 0))}")
    print("\n  TOP DEBTORS (by outstanding)")
    for p, v in rep["top"][:10]:
        print(f"    {inr(v):>16}   {p}")
    print("=" * 60 + "\n")


def write_csvs(invoices: list[Invoice], rep: dict) -> None:
    with open("dso_per_invoice.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["party", "invoice_date", "voucher", "amount", "received",
                    "outstanding", "closed_on", "collect_days", "status"])
        for i in sorted(invoices, key=lambda x: (x.party, x.date)):
            w.writerow([i.party, i.date, i.vch, f"{i.amount:.2f}", f"{i.received:.2f}",
                        f"{i.outstanding:.2f}", i.closed_on or "",
                        i.collect_days() if i.closed else "",
                        "paid" if i.closed else "open"])
    with open("dso_by_customer.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["party", "avg_collect_days", "invoices_paid", "invoices_open", "outstanding"])
        for p, d in sorted(rep["per_cust"].items(), key=lambda kv: -(kv[1]["outstanding"])):
            w.writerow([p, d["avg_days"] if d["avg_days"] is not None else "",
                        d["closed"], d["open"], f"{d['outstanding']:.2f}"])
    print("  Wrote dso_per_invoice.csv and dso_by_customer.csv")


# ── self-test (no files needed) ─────────────────────────────────────────────
def selftest() -> None:
    sales = [
        {"date": date(2025, 1, 1), "party": "ABC", "amount": 100000, "vch": "S1"},
        {"date": date(2025, 2, 1), "party": "XYZ", "amount": 50000, "vch": "S2"},
        {"date": date(2025, 3, 1), "party": "XYZ", "amount": 20000, "vch": "S3"},
    ]
    receipts = [  # ABC pays 100k across 3 dates -> closes 10-Mar (68 days)
        {"date": date(2025, 1, 20), "party": "ABC", "amount": 40000},
        {"date": date(2025, 2, 15), "party": "ABC", "amount": 30000},
        {"date": date(2025, 3, 10), "party": "ABC", "amount": 30000},
        {"date": date(2025, 2, 20), "party": "XYZ", "amount": 50000},  # closes S2 (19 days)
    ]
    invs = allocate_fifo(sales, receipts)
    by = {i.vch: i for i in invs}
    assert by["S1"].closed and by["S1"].collect_days() == 68, by["S1"].collect_days()
    assert by["S2"].closed and by["S2"].collect_days() == 19, by["S2"].collect_days()
    assert not by["S3"].closed and by["S3"].outstanding == 20000
    rep = report(invs, today=date(2025, 4, 1))
    # weighted avg over closed: (100k*68 + 50k*19)/150k = 51.67
    assert abs(rep["weighted_avg_days"] - 51.666) < 0.1, rep["weighted_avg_days"]
    assert rep["aging"]["31-60"] == 20000  # S3 raised 1-Mar, 31 days open by 1-Apr
    print("selftest OK: FIFO, collect-days, weighted DSO, aging all correct.")


def main(argv: list[str]) -> None:
    if "--selftest" in argv:
        selftest()
        return
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        raise SystemExit("usage: python dso_baseline.py sales.csv receipts.csv")
    today = date.today()
    for a in argv:
        if a.startswith("--today="):
            today = parse_date(a.split("=", 1)[1]) or today
    print("Reading Tally exports...")
    sales = read_rows(args[0], "sales")
    receipts = read_rows(args[1], "receipts")
    invoices = allocate_fifo(sales, receipts)
    rep = report(invoices, today)
    print_report(rep)
    write_csvs(invoices, rep)


if __name__ == "__main__":
    main(sys.argv[1:])
