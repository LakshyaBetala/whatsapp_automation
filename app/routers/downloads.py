"""Software download page + gated file serving.

GET /download            -> page: current version + (with a valid token) a button.
GET /download/<file>?token=  -> the actual zip.

The zip is GATED: the shop build still carries the Supabase key, so only an
onboarded shop (a valid agent_token) or the operator (the admin key) may pull it.
You get a ready-to-send download link on the Add Business screen. This gate is
removed once the shop becomes a credential-free thin client.

Put the built ASVA_shop.zip in settings.downloads_dir (default C:/ASVA/downloads)
on the host. Version control: the page shows the latest app_releases version, and
each running shop learns "update available" from its own /license/heartbeat.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.config import settings
from app.db import get_client

router = APIRouter(tags=["download"])

# Public download name -> real file on disk. Only these may be fetched.
ALLOWED = {
    "ASVA_shop.zip": "ASVA_shop.zip",
    "ASVA-Setup.exe": "ASVA-Setup.exe",
}

# Downloads that need NO token. The installer carries no secret: no database
# key, no agent token, no config. A fresh install knows nothing until the owner
# types a pairing code, so gating it would only break the website's Download
# button for no security gain. The legacy zip stays gated - it still ships the
# service-role key.
PUBLIC = {"ASVA-Setup.exe"}


def _path(real: str) -> str:
    return os.path.join(settings.downloads_dir or "downloads", real)


def _token_ok(token: str | None) -> bool:
    """A download is allowed for the operator (admin key) or any onboarded shop
    (a real agent_token). Keeps the key-bearing zip off open public access."""
    if not token:
        return False
    admin = (settings.admin_api_key or "").strip()
    if admin and secrets.compare_digest(token, admin):
        return True
    db = get_client()
    if db is None:
        return False
    try:
        r = (db.table("businesses").select("id")
             .eq("agent_token", token).limit(1).execute())
        return bool(r.data)
    except Exception:
        return False


def _newest_installer() -> str | None:
    """The exe the auto-update feed currently serves, read from updates/latest.yml
    (its 'path:' names the current build). Single source of truth so the website
    Download button always matches the published version and never goes stale.
    Falls back to the legacy stable file if the feed is not published."""
    updates_dir = os.path.join(settings.downloads_dir or "downloads", "updates")
    yml = os.path.join(updates_dir, "latest.yml")
    try:
        with open(yml, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("path:"):
                    fn = s.split(":", 1)[1].strip().strip('"\'')
                    if fn and "/" not in fn and "\\" not in fn and ".." not in fn:
                        p = os.path.join(updates_dir, fn)
                        if os.path.exists(p):
                            return p
                    break
    except Exception:
        pass
    legacy = _path("ASVA-Setup.exe")
    return legacy if os.path.exists(legacy) else None


def _latest_version() -> str:
    db = get_client()
    if db is not None:
        try:
            r = (db.table("app_releases").select("version")
                 .order("created_at", desc=True).limit(1).execute()).data
            if r:
                return str(r[0]["version"])
        except Exception:
            pass
    return settings.app_version


@router.get("/download/latest")
def download_latest():
    """Public installer, redirect to the CURRENT versioned exe (immutable URL) with
    no-store. Versioned targets never go stale in a CDN cache, and no-store keeps
    the redirect itself always pointing at the newest build - so the website button
    can never serve an old installer (the bug where a shop got 1.9.6 while the feed
    was on 2.0.4). Falls back to serving the legacy stable file if unpublished."""
    updates_dir = os.path.join(settings.downloads_dir or "downloads", "updates")
    yml = os.path.join(updates_dir, "latest.yml")
    fn = None
    try:
        with open(yml, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("path:"):
                    cand = s.split(":", 1)[1].strip().strip('"\'')
                    if cand and "/" not in cand and "\\" not in cand and ".." not in cand \
                            and os.path.exists(os.path.join(updates_dir, cand)):
                        fn = cand
                    break
    except Exception:
        fn = None
    if fn:
        r = RedirectResponse(url=f"/updates/{fn}", status_code=302)
        r.headers["Cache-Control"] = "no-store, max-age=0"
        return r
    p = _newest_installer()
    if not p or not os.path.exists(p):
        raise HTTPException(status_code=404,
                            detail="Not available yet - the host has not published this file.")
    resp = FileResponse(p, filename="ASVA-Setup.exe",
                        media_type="application/vnd.microsoft.portable-executable")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@router.get("/download/{name}")
def download_file(name: str, token: str = Query("")):
    real = ALLOWED.get(name)
    if not real:
        raise HTTPException(status_code=404, detail="Unknown download")
    if name not in PUBLIC and not _token_ok(token):
        raise HTTPException(status_code=403,
                            detail="This download needs your ASVA link. Ask your ASVA contact for it.")
    # The installer button always serves the CURRENT published build (the exe the
    # auto-update feed points to), so a new release never leaves it on an old file.
    p = _newest_installer() if name == "ASVA-Setup.exe" else _path(real)
    if not p or not os.path.exists(p):
        raise HTTPException(status_code=404,
                            detail="Not available yet - the host has not published this file.")
    media = ("application/vnd.microsoft.portable-executable"
             if real.lower().endswith(".exe") else "application/zip")
    return FileResponse(p, filename=real, media_type=media)


# ── Auto-update feed (electron-updater, generic provider) ───────────────────
# The desktop app polls this feed and updates itself in the background. Publish
# a new build by copying THREE files from dist_installer into
# <downloads_dir>/updates on the host:
#   latest.yml, ASVA-Setup-<version>.exe, ASVA-Setup-<version>.exe.blockmap
# latest.yml names the version + sha512; the .blockmap lets the app download
# only the changed bytes (small delta), so "one push to the i3" quietly updates
# every shop. These files carry NO secret (same as the public installer), so the
# feed is open - a token gate would only break the auto-updater for no gain.
_UPDATE_EXT = (".yml", ".exe", ".blockmap")


def _updates_path(name: str) -> str:
    base = os.path.join(settings.downloads_dir or "downloads", "updates")
    return os.path.join(base, name)


@router.get("/updates/{name}")
def update_feed_file(name: str):
    # Hard-lock the name: no path traversal, only the feed's own file types.
    if "/" in name or "\\" in name or ".." in name or not name.lower().endswith(_UPDATE_EXT):
        raise HTTPException(status_code=404, detail="Unknown file")
    p = _updates_path(name)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="Not published yet")
    if name.lower().endswith(".exe"):
        media = "application/vnd.microsoft.portable-executable"
    elif name.lower().endswith(".yml"):
        media = "text/yaml"
    else:
        media = "application/octet-stream"
    return FileResponse(p, filename=name, media_type=media)


@router.get("/download", response_class=HTMLResponse)
def download_page(token: str = Query("")):
    from app.site import WA_TRY, page_shell
    ver = _latest_version()
    p = _path("ASVA-Setup.exe")
    ready = os.path.exists(p)
    size = f"{os.path.getsize(p) / 1e6:.0f} MB" if ready else ""
    # The installer is public and carries no secret, so the button always works.
    if ready:
        btn = (f'<a class="btn btn-p" href="/download/latest">'
               f'Download ASVA for Windows ({size})</a>')
        note = ''
    else:
        btn = (f'<a class="btn btn-p" href="{WA_TRY}">Talk to us on WhatsApp</a>')
        note = ('<p class="undernote" style="color:#8a5a00">The new installer is being published. '
                'Message us and we will set you up right away.</p>')
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Download &middot; Version {ver}</span>
  <h1>Download ASVA for Windows</h1>
  <p class="lede">One installer. It reads your TallyPrime and sends bills and reminders on
    WhatsApp from your own number. You need Windows 10 or 11, TallyPrime, and the setup
    code we give you. Nothing else to install.</p>
  <div class="cta-row">{btn}</div>{note}
 </section>
 <section>
  <div class="sechead"><span class="eyebrow">Setup</span><h2>Three steps, about five minutes</h2></div>
  <div class="flow reveal">
   <div class="row"><div class="idx">1</div><div><h3>Run the installer</h3>
     <p>Open the file you just downloaded and it installs itself. No settings to choose,
       and nothing else to install first.</p></div></div>
   <div class="row"><div class="idx">2</div><div><h3>Type your setup code</h3>
     <p>We read you a short code such as <b>K7P2-9M4T</b>. Type it in and ASVA connects
       itself to your shop. There is nothing to copy or paste.</p></div></div>
   <div class="row"><div class="idx">3</div><div><h3>Pick your company and scan WhatsApp</h3>
     <p>Choose your company from the list ASVA reads out of Tally, then scan the square with
       your shop's phone, the same way you use WhatsApp Web. That is the whole setup.</p></div></div>
  </div>
  <p class="undernote">Don't have a setup code yet? <a href="{WA_TRY}" style="color:var(--accent)">Message us</a> and we will get you started.</p>
 </section>
</div>"""
    return HTMLResponse(page_shell(
        path="/download",
        title="Download ASVA for Windows | Tally to WhatsApp collections",
        description="Download the ASVA shop app for Windows. It reads TallyPrime and sends bills and payment reminders on WhatsApp from your own number. Setup takes a few minutes.",
        body=body))
