"""Build the ASVA deployment zips.

Produces TWO zips on the Desktop:

  ASVA_shop.zip  - father's laptop: backend + ONE shop WhatsApp + Tally agent +
                   the Electron app. Everything the shop pilot needs. Its .env
                   has PLATFORM_WA_URL blanked so the digest/alerts use the shop
                   number (the bot laptop is separate).

  ASVA_bot.zip   - the spare/old laptop: backend (digest-only) + the bot
                   WhatsApp service + ASVA_BOT.bat. Native, no Docker, no Tally,
                   no 54MB agent exe. Small and self-contained.

Both walk the working tree (current files, committed or not) and exclude
dev/heavy/junk dirs (node_modules, .venv, browser caches, __pycache__, .git).

    python build_zip.py            # writes both to ~/Desktop
"""
from __future__ import annotations

import json
import os
import secrets
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

SKIP_DIRS = {
    "node_modules", ".venv", "venv", ".git", "__pycache__", ".pytest_cache",
    ".wwebjs_auth", ".wwebjs_cache", ".baileys_auth", "dist", "build", ".idea", ".vscode",
    ".mypy_cache", ".ruff_cache",
}
SKIP_SUFFIX = (".pyc", ".pyo", ".zip", ".log", ".spec", ".bak")
SKIP_NAMES = {".DS_Store", "Thumbs.db"}

# Files that belong ONLY to the bot (kept out of the shop zip).
BOT_ONLY = {"ASVA_BOT.bat", ".env.bot", "BOT_SETUP.md"}

# The bot zip is a minimal subset: these top-level dirs + files only.
BOT_DIRS = ("app/", "wa_service/", "migrations/")
BOT_FILES = {"ASVA_BOT.bat", ".env.bot", "requirements.txt", "BOT_SETUP.md", "TEST_GUIDE.md"}

# --- Server-host split (the i3 always-on laptop = server; shop = thin agent) ---
# The HOST runs everything except reading Tally: backend + scheduler + Command
# Center + the shop's WhatsApp session. It does NOT need the 54MB agent exe or
# the Electron app, so we drop those top dirs to stay lean.
HOST_SKIP_TOP = {"Asva", "tally_agent", "desktop", "dashboard"}
# Shop / bot / dev launchers that don't belong on the host.
HOST_SKIP_FILES = {"START.bat", "ASVA.vbs", "DASHBOARD.bat", "AGENT_ONLY.bat",
                   "SHOP_AGENT_SETUP.md", "build_zip.py"} | BOT_ONLY

# The SHOP CLIENT is the thin agent: read Tally, push to the host. Nothing else.
CLIENT_DIRS = ("tally_agent/", "Asva/")
CLIENT_FILES = {"AGENT_ONLY.bat", "SHOP_AGENT_SETUP.md"}
# The live per-machine configs never travel in a generic build - we ship a
# clean template instead so no token leaks between shops.
CLIENT_SKIP = {"tally_agent/config.json", "Asva/config.json"}

# Host env overrides: it sends directly (WhatsApp is here), runs the scheduler,
# and the Command Center is ON. Set ADMIN_API_KEY to the operator key.
def _admin_key() -> str:
    """A stable operator key for the Command Center. Persisted to .admin_key
    (gitignored) so rebuilding the host zip keeps the same key - the operator
    bookmarks /ops?key=... once and it keeps working."""
    p = os.path.join(ROOT, ".admin_key")
    if os.path.exists(p):
        k = open(p, encoding="utf-8").read().strip()
        if k:
            return k
    k = secrets.token_urlsafe(24)
    with open(p, "w", encoding="utf-8") as f:
        f.write(k + "\n")
    return k


def _skip(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    name = parts[-1]
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIX)


def _walk():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, ROOT).replace("\\", "/")
            if not _skip(rel):
                yield ap, rel


def _shop_env() -> str:
    """The shop .env: same as the working .env but with a couple of keys forced
    blank on a distributed shop laptop:
      - PLATFORM_WA_URL: no bot here -> digest/alerts fall back to the shop number
      - ADMIN_API_KEY: the Command Center (/ops) reads the SHARED database, so it
        must NEVER be enabled on a shop laptop - only on the operator's own
        machine. Blanking it here keeps /ops OFF on every shipped shop install."""
    blanks = ("PLATFORM_WA_URL", "ADMIN_API_KEY")
    src = os.path.join(ROOT, ".env")
    out = []
    seen = set()
    for line in open(src, encoding="utf-8").read().splitlines():
        key = line.strip().split("=", 1)[0].strip()
        if key in blanks:
            out.append(f"{key}=")
            seen.add(key)
        else:
            out.append(line)
    for k in blanks:
        if k not in seen:
            out.append(f"{k}=")
    return "\n".join(out) + "\n"


def _server_env(admin_key: str) -> str:
    """The host .env: the working .env plus the values that make this box the
    server - Command Center ON, scheduler ON, sends go out directly from the
    shop WhatsApp session running here (not queued)."""
    forced = {
        "ADMIN_API_KEY": admin_key,
        "ENABLE_REMINDER_SWEEP": "true",
        "ENABLE_EOD_DIGEST": "true",
        "ENABLE_SUBSCRIPTION_CHECK": "true",
        "SEND_VIA_OUTBOX": "false",     # WhatsApp is here -> send directly
        "ENABLE_OUTBOX_SEND": "true",   # also drain any queued sends
    }
    src = os.path.join(ROOT, ".env")
    out, seen = [], set()
    for line in open(src, encoding="utf-8").read().splitlines():
        key = line.strip().split("=", 1)[0].strip()
        if key in forced:
            out.append(f"{key}={forced[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in forced.items():
        if k not in seen:
            out.append(f"{k}={v}")
    return "\n".join(out) + "\n"


def _client_config_template() -> str:
    """A clean config.json for a shop laptop - operator fills the 3 values from
    the Add Business screen. No real tokens ever ship in a generic build."""
    return json.dumps({
        "backend_url": "https://asva.YOURDOMAIN.com",
        "business_id": "PASTE_FROM_ADD_BUSINESS",
        "agent_token": "PASTE_FROM_ADD_BUSINESS",
        "company_name": "YOUR TALLY COMPANY NAME",
        "tally_host": "localhost",
        "tally_port": 9000,
        "watch_interval_seconds": 120,
        "bill_pdf_dir": "C:\\ASVA\\bills",
    }, indent=2) + "\n"


def build_server() -> None:
    """The always-on host zip: backend + scheduler + Command Center + shop
    WhatsApp. Command Center is ENABLED here (operator box, shared DB)."""
    key = _admin_key()
    out = os.path.join(DESKTOP, "ASVA_server.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ap, rel in _walk():
            top = rel.split("/", 1)[0]
            if top in HOST_SKIP_TOP or rel in HOST_SKIP_FILES:
                continue
            if rel == ".env":
                z.writestr(".env", _server_env(key))
            else:
                z.write(ap, rel)
            n += 1
    _report("ASVA_server.zip", out, n,
            ("HOST_START.bat", "TUNNEL.bat", "KEEP_AWAKE.bat", "HOST_SETUP.md",
             "app/main.py", "wa_service/index.js", ".env"))
    print(f"  Command Center key (also saved to .admin_key): {key}")
    print(f"  Open after start:  http://localhost:8000/ops?key={key}")


def build_shop_client() -> None:
    """The thin shop zip: Tally agent only, points at the host. No backend,
    no WhatsApp, no admin key, no database."""
    out = os.path.join(DESKTOP, "ASVA_shop_client.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ap, rel in _walk():
            if rel in CLIENT_SKIP:
                continue
            keep = rel in CLIENT_FILES or any(rel.startswith(d) for d in CLIENT_DIRS)
            if keep:
                z.write(ap, rel)
                n += 1
        z.writestr("tally_agent/config.json", _client_config_template())
        n += 1
    _report("ASVA_shop_client.zip", out, n,
            ("AGENT_ONLY.bat", "SHOP_AGENT_SETUP.md", "tally_agent/agent.py",
             "tally_agent/config.json", "Asva/Asva.exe"))


def build_shop() -> None:
    out = os.path.join(DESKTOP, "ASVA_shop.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ap, rel in _walk():
            if rel in BOT_ONLY or rel == "build_zip.py":
                continue
            if rel == ".env":
                z.writestr(".env", _shop_env())   # transformed
            else:
                z.write(ap, rel)
            n += 1
    _report("ASVA_shop.zip", out, n,
            ("SETUP.bat", "START.bat", "ASVA.vbs", "Asva/Asva.exe",
             "tally_agent/agent.py", "desktop/main.js", ".env"))


def build_bot() -> None:
    out = os.path.join(DESKTOP, "ASVA_bot.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ap, rel in _walk():
            keep = rel in BOT_FILES or any(rel.startswith(d) for d in BOT_DIRS)
            if keep:
                z.write(ap, rel)
                n += 1
    _report("ASVA_bot.zip", out, n,
            ("ASVA_BOT.bat", ".env.bot", "BOT_SETUP.md", "requirements.txt",
             "app/main.py", "wa_service/index.js"))


def _report(label: str, path: str, count: int, musts) -> None:
    names = set(zipfile.ZipFile(path).namelist())
    size = os.path.getsize(path) / (1024 * 1024)
    print(f"\n{label}: {count} files, {size:.1f} MB")
    for m in musts:
        print(("  ok  " if m in names else "  MISSING  ") + m)


if __name__ == "__main__":
    import sys
    which = set(sys.argv[1:]) or {"shop", "bot"}
    if "all" in which:
        which = {"shop", "bot", "server", "client"}
    if "shop" in which:
        build_shop()
    if "bot" in which:
        build_bot()
    if "server" in which:
        build_server()
    if "client" in which:
        build_shop_client()
