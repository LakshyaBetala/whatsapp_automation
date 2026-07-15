"""Public software download page + file serving, on the website.

GET /download        -> a simple page: current version + a Download button.
GET /download/<file> -> the actual zip (only an allow-listed name, no traversal).

Put the built ASVA_shop.zip in settings.downloads_dir (default C:/ASVA/downloads)
on the host. Version control: the page shows the latest app_releases version, and
each running shop learns "update available" from its own /license/heartbeat, so a
shop is nudged to re-download when you ship a new build + insert a new release row.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.config import settings
from app.db import get_client

router = APIRouter(tags=["download"])

# Public download name -> real file on disk. Only these may be fetched.
ALLOWED = {
    "ASVA_shop.zip": "ASVA_shop.zip",
}


def _path(real: str) -> str:
    return os.path.join(settings.downloads_dir or "downloads", real)


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
def download_file(name: str):
    real = ALLOWED.get(name)
    if not real:
        raise HTTPException(status_code=404, detail="Unknown download")
    p = _path(real)
    if not os.path.exists(p):
        raise HTTPException(status_code=404,
                            detail="Not available yet - the host has not published this file.")
    return FileResponse(p, filename=real, media_type="application/zip")


@router.get("/download", response_class=HTMLResponse)
def download_page():
    from app.site import WA_TRY, page_shell
    ver = _latest_version()
    p = _path("ASVA_shop.zip")
    ready = os.path.exists(p)
    size = f"{os.path.getsize(p) / 1e6:.0f} MB" if ready else ""
    btn = (f'<a class="btn btn-p" href="/download/ASVA_shop.zip">Download ASVA for Windows ({size})</a>'
           if ready else
           '<div class="undernote" style="color:#8a5a00">The download is being published. Please check back shortly.</div>')
    body = f"""<div class="wrap">
 <section class="page-hero reveal">
  <span class="eyebrow">Download &middot; Version {ver}</span>
  <h1>Download ASVA for Windows</h1>
  <p class="lede">The shop app reads your TallyPrime and sends bills and reminders on WhatsApp
    from your own number. Windows 10 or 11, TallyPrime, and your ASVA licence details.</p>
  <div class="cta-row">{btn}<a class="btn btn-s" href="{WA_TRY}">Get my licence</a></div>
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
