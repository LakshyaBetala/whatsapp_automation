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


def _env_transform(overrides: dict, ensure: dict | None = None) -> str:
    """The working .env with each key in `overrides` forced to the given value,
    and any key in `ensure` appended (blank/default) only if it is absent -
    never overwriting a real value the operator already set."""
    ensure = ensure or {}
    src = os.path.join(ROOT, ".env")
    out, seen = [], set()
    for line in open(src, encoding="utf-8").read().splitlines():
        key = line.strip().split("=", 1)[0].strip()
        out.append(f"{key}={overrides[key]}" if key in overrides else line)
        seen.add(key)
    for k, v in {**overrides, **ensure}.items():
        if k not in seen:
            out.append(f"{k}={v}")
    return "\n".join(out) + "\n"


def _shop_env() -> str:
    """Shop laptop (e.g. father's): runs the shop's OWN WhatsApp (scanned by the
    shopkeeper, port 3001) and DELIVERS the host-queued customer sends from that
    number. The always-on i3 host owns timing (reminders, digest, subscription),
    so those jobs are OFF here - otherwise both boxes would send. Command Center
    is OFF (it reads the shared DB; operator machine only)."""
    return _env_transform({
        "ENABLE_REMINDER_SWEEP": "false",     # host computes + queues reminders
        "ENABLE_EOD_DIGEST": "false",         # host bot sends the owner digest
        "ENABLE_SUBSCRIPTION_CHECK": "false",  # host runs the subscription clock
        "SEND_VIA_OUTBOX": "false",           # this IS the shop number -> direct
        "ENABLE_OUTBOX_SEND": "true",         # drain the host's queue from here
        "PLATFORM_WA_URL": "",                # owner alerts come from the host bot
        "ADMIN_API_KEY": "",                  # never a Command Center on a shop
    })


def _server_env(admin_key: str) -> str:
    """i3 host = server + bot. The always-on backend owns timing (reminders,
    digest, subscription) and the Command Center. Customer sends are QUEUED
    (send_via_outbox) for the shop to deliver from its own number; the host has
    no shop number, so it does NOT drain the queue. Owner-facing messages (digest,
    alerts, bot replies) go via the BOT WhatsApp on :3002 (you scan that one)."""
    return _env_transform({
        "ADMIN_API_KEY": admin_key,
        "PUBLIC_BASE_URL": "https://tryasva.com",     # landing + API + /ops, all on the i3
        "PLATFORM_WA_URL": "http://localhost:3002",  # the bot number (owner-facing)
        "ENABLE_REMINDER_SWEEP": "true",
        "ENABLE_EOD_DIGEST": "true",
        "ENABLE_SUBSCRIPTION_CHECK": "true",
        "SEND_VIA_OUTBOX": "true",            # queue customer sends for the shop
        "ENABLE_OUTBOX_SEND": "false",        # no shop number here to deliver
        "OPERATOR_UPI_ID": "9344110272@ybl",  # where shops pay ASVA (renewal pay-link)
        "OPERATOR_UPI_NAME": "ASVA",
    }, ensure={  # Health-center email alerts (Gmail app password). Blank = alerts
               # still show in /ops, just not emailed.
               "ALERT_EMAIL_TO": "", "ALERT_EMAIL_FROM": "",
               "SMTP_HOST": "", "SMTP_PORT": "587",
               "SMTP_USER": "", "SMTP_PASS": ""})


def _client_config_template() -> str:
    """A clean config.json for a shop laptop - operator fills the 3 values from
    the Add Business screen. No real tokens ever ship in a generic build."""
    return json.dumps({
        "backend_url": "https://tryasva.com",
        "business_id": "PASTE_FROM_ADD_BUSINESS",
        "agent_token": "PASTE_FROM_ADD_BUSINESS",
        "company_name": "YOUR TALLY COMPANY NAME",
        "tally_host": "localhost",
        "tally_port": 9000,
        "watch_interval_seconds": 300,
        "folder_poll_seconds": 8,
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


def _solo_env() -> str:
    """The ALL-IN-ONE standalone shop build: ONE laptop does everything and needs
    NO central server. The scheduler + digest run locally and sends go out
    directly from the shop's own WhatsApp. This is the pre-server-split behaviour
    - use it while the i3 host is not set up yet."""
    return _env_transform({
        "ENABLE_REMINDER_SWEEP": "true",     # this laptop computes + sends reminders
        "ENABLE_EOD_DIGEST": "true",         # and the owner digest
        "ENABLE_SUBSCRIPTION_CHECK": "false",  # no subscription nagging on a solo box
        "SEND_VIA_OUTBOX": "false",          # send directly from the shop WhatsApp
        "ENABLE_OUTBOX_SEND": "false",       # nothing is queued -> no drainer needed
        "ENABLE_MONITOR": "false",           # the health watchdog is operator-side
        "PLATFORM_WA_URL": "",               # no bot number -> owner msgs via shop WA
        "ADMIN_API_KEY": "",                 # no Command Center on a shop
    })


def build_standalone() -> None:
    """ASVA_standalone.zip - the full app on one laptop, server-independent."""
    out = os.path.join(DESKTOP, "ASVA_standalone.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ap, rel in _walk():
            if rel in BOT_ONLY or rel == "build_zip.py":
                continue
            if rel == ".env":
                z.writestr(".env", _solo_env())
            else:
                z.write(ap, rel)
            n += 1
    _report("ASVA_standalone.zip", out, n,
            ("SETUP.bat", "START.bat", "ASVA.vbs", "Asva/Asva.exe",
             "tally_agent/agent.py", "desktop/main.js", ".env"))


def build_landing() -> None:
    """ASVA_landing.zip = a single index.html (the marketing site) you can host
    anywhere (Vercel, Netlify, any static host) at tryasva.com. The i3 also
    serves this same page at GET /, so hosting it statically is optional."""
    try:
        import sys as _sys
        _sys.path.insert(0, ROOT)
        from app.landing import landing_html
        html = landing_html()
    except Exception as e:  # never block the other builds
        print(f"  landing skipped: {e}")
        return
    out = os.path.join(DESKTOP, "ASVA_landing.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("index.html", html)
    _report("ASVA_landing.zip", out, 1, ("index.html",))


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
        which |= {"shop", "bot", "server", "client", "landing"}
    if "landing" in which:
        build_landing()
    if "standalone" in which or "solo" in which:
        build_standalone()
    if "shop" in which:
        build_shop()
    if "bot" in which:
        build_bot()
    if "server" in which:
        build_server()
    if "client" in which:
        build_shop_client()
