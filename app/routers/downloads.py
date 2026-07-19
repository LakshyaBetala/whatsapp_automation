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
from fastapi.responses import FileResponse, HTMLResponse

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


@router.get("/download/{name}")
def download_file(name: str, token: str = Query("")):
    real = ALLOWED.get(name)
    if not real:
        raise HTTPException(status_code=404, detail="Unknown download")
    if name not in PUBLIC and not _token_ok(token):
        raise HTTPException(status_code=403,
                            detail="This download needs your ASVA link. Ask your ASVA contact for it.")
    p = _path(real)
    if not os.path.exists(p):
        raise HTTPException(status_code=404,
                            detail="Not available yet - the host has not published this file.")
    media = ("application/vnd.microsoft.portable-executable"
             if real.lower().endswith(".exe") else "application/zip")
    return FileResponse(p, filename=real, media_type=media)


@router.get("/download", response_class=HTMLResponse)
def download_page(token: str = Query("")):
    from app.site import WA_TRY, page_shell
    ver = _latest_version()
    p = _path("ASVA_shop.zip")
    ready = os.path.exists(p)
    size = f"{os.path.getsize(p) / 1e6:.0f} MB" if ready else ""
    if not _token_ok(token):
        btn = (f'<a class="btn btn-p" href="{WA_TRY}">Get my download link</a>')
        note = ('<p class="undernote" style="color:#8a5a00">This download opens with the personal link '
                'from your ASVA setup. Message us and we will onboard you and send it.</p>')
    elif ready:
        btn = (f'<a class="btn btn-p" href="/download/ASVA_shop.zip?token={token}">Download ASVA for Windows ({size})</a>')
        note = ''
    else:
        btn = '<div class="undernote" style="color:#8a5a00">The download is being published. Please check back shortly.</div>'
        note = ''
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Download &middot; Version {ver}</span>
  <h1>Download ASVA for Windows</h1>
  <p class="lede">The shop app reads your TallyPrime and sends bills and reminders on WhatsApp
    from your own number. Windows 10 or 11, TallyPrime, and your ASVA licence details.</p>
  <div class="cta-row">{btn}</div>{note}
 </section>
 <section>
  <div class="sechead"><span class="eyebrow">Setup</span><h2>Up and running in a few minutes</h2></div>
  <div class="flow reveal">
   <div class="row"><div class="idx">1</div><div><h3>Unzip to C:\\ASVA</h3>
     <p>Extract the download to the <b>C:\\ASVA</b> folder on the shop PC.</p></div></div>
   <div class="row"><div class="idx">2</div><div><h3>Paste your licence</h3>
     <p>Open <b>tally_agent\\config.json</b> and paste the <b>business id</b> and <b>agent token</b> from your ASVA setup, then set your Tally company name.</p></div></div>
   <div class="row"><div class="idx">3</div><div><h3>Run SETUP, then START</h3>
     <p>Run <b>SETUP.bat</b> once, then <b>START.bat</b> each day. ASVA keeps itself updated from here on.</p></div></div>
   <div class="row"><div class="idx">4</div><div><h3>Scan WhatsApp</h3>
     <p>Open <b>localhost:3001/qr</b> and scan with the shop's phone. That links your own number for bills and reminders.</p></div></div>
  </div>
  <p class="undernote">Don't have your licence details yet? <a href="{WA_TRY}" style="color:var(--accent)">Message us</a> and we will set you up.</p>
 </section>
</div>"""
    return HTMLResponse(page_shell(
        path="/download",
        title="Download ASVA for Windows | Tally to WhatsApp collections",
        description="Download the ASVA shop app for Windows. It reads TallyPrime and sends bills and payment reminders on WhatsApp from your own number. Setup takes a few minutes.",
        body=body))
