"""BUSY discovery - run ONCE on a machine that has BUSY, send back the JSON.

    python discover.py                 -> busy_discovery.json next to this file
    python discover.py --company "X"   -> also dumps that company's schema

READ-ONLY. It never writes to BUSY's data, never opens a company for writing,
and never sends anything anywhere. It answers the questions we cannot answer
without a real install:

  1. Where is BUSY, which version, and where is its data directory?
  2. Is each company an Access file (.mdb / .bds) or a SQL Server database?
  3. Which ODBC drivers are present (Access? SQL Server?) - i.e. can Python read it?
  4. For one company: every table, its columns, and a row count, with the
     tables that LOOK like masters / vouchers / bills flagged.

Why this and not BUSY's API: BUSY's own FAQ says it "does not have any default
option for integration of any third party software" - the API is channel-partner
only with no public spec. The database is the documented, stable surface, and it
is what BUSY add-on vendors read. Tally's XML facts in tally_xml.py all came from
a live Tally; this is the equivalent step for BUSY.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import sys
from datetime import datetime

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "busy_discovery.json")

# Every engine a BUSY product might use. Classic BUSY: Access (.mdb/.bds) or
# SQL Server. BUSY Magic is newer and undocumented, so also catch SQLite
# (.db/.sqlite), SQL Server LocalDB (.mdf) and Firebird (.fdb).
DATA_EXT = (".mdb", ".bds", ".accdb", ".db", ".sqlite", ".sqlite3", ".mdf", ".fdb")

# Words that, in a table name, suggest the tables we need. Case-insensitive.
HINTS = {
    "party_masters": ["master", "party", "ledger", "account", "acc"],
    "vouchers": ["voucher", "vch", "tran", "trans", "sale", "invoice"],
    "bill_wise": ["bill", "ref", "outstanding", "adj", "pending"],
    "receipts": ["receipt", "rcpt", "payment", "collection"],
    "groups": ["group", "grp"],
}


def _registry_hits() -> list[dict]:
    """BUSY install + data-dir hints from the Windows registry (best-effort)."""
    hits = []
    if platform.system() != "Windows":
        return hits
    try:
        import winreg
    except ImportError:
        return hits
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE"), (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE")]
    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i); i += 1
                    except OSError:
                        break
                    if "busy" not in name.lower():
                        continue
                    entry = {"key": f"{base}\\{name}", "values": {}}
                    try:
                        with winreg.OpenKey(hive, f"{base}\\{name}") as sk:
                            j = 0
                            while True:
                                try:
                                    vn, vv, _ = winreg.EnumValue(sk, j); j += 1
                                    if isinstance(vv, (str, int)):
                                        entry["values"][vn] = vv
                                except OSError:
                                    break
                    except OSError:
                        pass
                    hits.append(entry)
        except OSError:
            continue
    return hits


def _find_installs() -> list[str]:
    cands = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 r"C:\Busy", r"C:\BusyWin", r"D:\Busy", r"D:\BusyWin"):
        if not root or not os.path.isdir(root):
            continue
        for d in glob.glob(os.path.join(root, "*usy*")):
            if os.path.isdir(d):
                cands.append(d)
    return sorted(set(cands))


def _find_data_files(install_dirs: list[str], registry: list[dict]) -> list[dict]:
    """Company databases (.mdb/.bds/.accdb). Searches ONLY BUSY's own places -
    the install dirs, any data directory named in the registry, and the usual
    BusyData roots - never a whole drive, which took minutes on a big disk."""
    roots: list[str] = list(install_dirs)
    for hit in registry:
        for v in hit.get("values", {}).values():
            if isinstance(v, str) and os.path.isdir(v):
                roots.append(v)
    for c in "CDEF":
        for name in ("BusyData", "BUSYWIN", "BusyWin", "Busy", "BUSY", "BusyMagic", "BUSY Magic"):
            d = os.path.join(f"{c}:\\", name)
            if os.path.isdir(d):
                roots.append(d)
    # BUSY Magic is a newer product and may keep data where modern Windows apps
    # do, not in C:\BusyData. Look in every per-user / per-machine app folder.
    for env in ("ProgramData", "LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        base = os.environ.get(env)
        if not base or not os.path.isdir(base):
            continue
        for sub in ("", "Documents"):
            top = os.path.join(base, sub) if sub else base
            try:
                for d in os.listdir(top):
                    if "usy" in d.lower() and os.path.isdir(os.path.join(top, d)):
                        roots.append(os.path.join(top, d))
            except OSError:
                pass
    found = []
    seen_roots = set()
    for root in roots:
        r = os.path.normpath(root).lower()
        if r in seen_roots:
            continue
        seen_roots.add(r)
        for dirpath, dirnames, filenames in os.walk(root):
            # never descend into huge irrelevant trees
            dirnames[:] = [d for d in dirnames if d.lower() not in ("node_modules", "windows", "$recycle.bin")]
            if dirpath.count(os.sep) - root.count(os.sep) > 6:
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.lower().endswith(DATA_EXT):
                    f = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(f)
                        found.append({"path": f, "bytes": st.st_size,
                                      "modified": datetime.fromtimestamp(st.st_mtime).isoformat()})
                    except OSError:
                        pass
    seen, out = set(), []
    for x in sorted(found, key=lambda r: -r["bytes"]):
        if x["path"].lower() not in seen:
            seen.add(x["path"].lower()); out.append(x)
    return out[:40]


def _odbc_drivers() -> list[str]:
    try:
        import pyodbc
        return sorted(pyodbc.drivers())
    except ImportError:
        return ["(pyodbc not installed - pip install pyodbc)"]


def _classify(table: str) -> list[str]:
    t = table.lower()
    return [kind for kind, words in HINTS.items() if any(w in t for w in words)]


def _dump_access(path: str, max_tables: int = 400) -> dict:
    """Schema + row counts of one Access company file, READ-ONLY."""
    try:
        import pyodbc
    except ImportError:
        return {"error": "pyodbc not installed"}
    drv = next((d for d in pyodbc.drivers() if "Microsoft Access Driver" in d), None)
    if not drv:
        return {"error": "no Microsoft Access ODBC driver - install 'Microsoft Access Database Engine 2016 Redistributable'"}
    conn_str = f"DRIVER={{{drv}}};DBQ={path};ReadOnly=1;"
    out = {"driver": drv, "tables": []}
    try:
        cn = pyodbc.connect(conn_str, readonly=True, autocommit=True)
    except Exception as e:
        # BUSY may password-protect the file; report it rather than guess.
        return {"error": f"open failed: {e}"[:300]}
    try:
        cur = cn.cursor()
        names = [r.table_name for r in cur.tables(tableType="TABLE")]
        for name in names[:max_tables]:
            entry = {"name": name, "looks_like": _classify(name), "columns": [], "rows": None}
            try:
                cols = cur.columns(table=name)
                entry["columns"] = [{"name": c.column_name, "type": c.type_name} for c in cols][:80]
            except Exception as e:
                entry["columns_error"] = str(e)[:120]
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{name}]")
                entry["rows"] = cur.fetchone()[0]
            except Exception as e:
                entry["rows_error"] = str(e)[:120]
            out["tables"].append(entry)
        out["table_count"] = len(names)
    finally:
        cn.close()
    return out


def _dump_sqlite(path: str, max_tables: int = 400) -> dict:
    """Schema + row counts of a SQLite file, READ-ONLY, no driver needed."""
    import sqlite3
    out = {"driver": "sqlite3 (stdlib)", "tables": []}
    try:
        cn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as e:
        return {"error": f"open failed: {e}"[:300]}
    try:
        cur = cn.cursor()
        names = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names[:max_tables]:
            entry = {"name": name, "looks_like": _classify(name), "columns": [], "rows": None}
            try:
                entry["columns"] = [{"name": r[1], "type": r[2]} for r in
                                    cur.execute(f'PRAGMA table_info("{name}")')][:80]
                entry["rows"] = cur.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception as e:
                entry["error"] = str(e)[:120]
            out["tables"].append(entry)
        out["table_count"] = len(names)
    finally:
        cn.close()
    return out


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _dump_company(path: str) -> dict:
    if _is_sqlite(path):
        return _dump_sqlite(path)
    if path.lower().endswith((".mdb", ".bds", ".accdb")):
        return _dump_access(path)
    return {"error": f"engine not recognised for {os.path.basename(path)} - send the file's first 64 bytes: "
                     + repr(open(path, "rb").read(64))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="path to ONE company .mdb/.bds to dump its schema")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    report = {
        "generated_at": datetime.now().isoformat(),
        "machine": {"os": platform.platform(), "python": sys.version.split()[0]},
        "registry": _registry_hits(),
        "install_dirs": _find_installs(),
        "odbc_drivers": _odbc_drivers(),
        "company_schema": None,
        "notes": [
            "Everything here is read-only. Nothing was changed in BUSY.",
            "If data_files is empty the company may be on SQL Server (client-server edition): "
            "note the SQL instance name shown in BUSY > Company > Company Info.",
            "If company_schema.error mentions a password, BUSY protects the file - we then read "
            "via BUSY's own ODBC/SQL route instead of the raw file.",
        ],
    }
    report["data_files"] = _find_data_files(report["install_dirs"], report["registry"])
    target = a.company
    if not target and report["data_files"]:
        target = report["data_files"][0]["path"]
    if target and os.path.isfile(target):
        report["company_schema"] = {"path": target, **_dump_company(target)}

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"BUSY discovery written to {a.out}")
    print(f"  install dirs : {len(report['install_dirs'])}")
    print(f"  data files   : {len(report['data_files'])}")
    print(f"  odbc drivers : {', '.join(report['odbc_drivers'])[:120]}")
    cs = report["company_schema"] or {}
    if cs.get("tables"):
        flagged = [t["name"] for t in cs["tables"] if t["looks_like"]]
        print(f"  schema       : {cs.get('table_count')} tables, {len(flagged)} look relevant: "
              f"{', '.join(flagged[:12])}{'...' if len(flagged) > 12 else ''}")
    elif cs.get("error"):
        print(f"  schema       : {cs['error']}")
    print("\nSend busy_discovery.json back - it contains no transaction data, only structure and counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
