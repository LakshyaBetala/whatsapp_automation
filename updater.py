"""ASVA self-updater - keeps every shop on the latest version automatically.

Run at every launch (START.bat and the desktop app both call it first). It asks
the server whether a newer version exists; if so it downloads the latest app and
applies it over this install, PRESERVING the things that are unique to this shop:
its .env, its tally_agent/config.json, and its WhatsApp login. Then the normal
launch continues with the new code. Safe to run every time - it does nothing when
already up to date, and never deletes the shop's data.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Files that are unique per shop - NEVER overwrite these on update.
KEEP_FILES = {".env", "tally_agent/config.json", "asva/config.json"}
# Directories to leave untouched (installed deps + the WhatsApp session + git).
KEEP_DIRS = (".venv", "wa_service/node_modules", "wa_service/.baileys_auth",
             ".baileys_auth", ".git", "downloads")


def _config() -> dict:
    with open(os.path.join(HERE, "tally_agent", "config.json"), encoding="utf-8") as f:
        return json.load(f)


def _get_json(url: str, timeout: int = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _skip(rel: str) -> bool:
    low = rel.lower()
    if low in KEEP_FILES:
        return True
    if any(low == d or low.startswith(d + "/") for d in KEEP_DIRS):
        return True
    # Never overwrite the batch file that may be running us (Windows can corrupt
    # a .bat that is executing). App code + services update fine; bats rarely
    # change and are refreshed by a fresh download when they do.
    if low.endswith(".bat") and "/" not in low:
        return True
    return False


def main() -> None:
    try:
        cfg = _config()
        base = cfg["backend_url"].rstrip("/")
        token = cfg["agent_token"]
    except Exception as e:
        print(f"[update] no config yet, skipping ({e})")
        return

    try:
        st = _get_json(f"{base}/license/status?token={urllib.parse.quote(token)}")
    except Exception as e:
        print(f"[update] server unreachable, keeping current version ({e})")
        return

    if not st.get("update_available"):
        print(f"[update] up to date (v{st.get('server_version')}).")
        return

    have, latest = st.get("server_version"), st.get("latest_version")
    print(f"[update] new version {latest} available (you have {have}). Updating now...")

    try:
        # The download is gated (the zip carries the DB key); authenticate with
        # this shop's own agent token, same as every other call to the host.
        dl = f"{base}/download/ASVA_shop.zip?token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(dl, timeout=180) as r:
            blob = r.read()
        z = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as e:
        print(f"[update] download failed, keeping current version ({e})")
        return

    changed_reqs = False
    applied = 0
    for name in z.namelist():
        rel = name.replace("\\", "/")
        if rel.endswith("/") or _skip(rel):
            continue
        dest = os.path.join(HERE, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            applied += 1
            if rel.lower() == "requirements.txt":
                changed_reqs = True
        except Exception as e:
            print(f"[update] could not write {rel} ({e})")

    # If Python dependencies changed, refresh the venv so the new code imports.
    if changed_reqs:
        py = os.path.join(HERE, ".venv", "Scripts", "python.exe")
        py = py if os.path.exists(py) else sys.executable
        try:
            print("[update] installing updated dependencies...")
            subprocess.run([py, "-m", "pip", "install", "-r",
                            os.path.join(HERE, "requirements.txt"), "--quiet"],
                           cwd=HERE, timeout=600)
        except Exception as e:
            print(f"[update] dependency install had an issue ({e})")

    print(f"[update] updated {applied} files to v{latest}. Starting the new version.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # an updater must NEVER block the app from starting
        print(f"[update] skipped ({e})")
