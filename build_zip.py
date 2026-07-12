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

import os
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
    """The shop .env: same as the working .env but with PLATFORM_WA_URL blanked
    (no bot on the shop laptop -> digest/alerts fall back to the shop number)."""
    src = os.path.join(ROOT, ".env")
    out = []
    seen = False
    for line in open(src, encoding="utf-8").read().splitlines():
        if line.strip().startswith("PLATFORM_WA_URL"):
            out.append("PLATFORM_WA_URL=")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append("PLATFORM_WA_URL=")
    return "\n".join(out) + "\n"


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
    build_shop()
    build_bot()
