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
    ver = _latest_version()
    p = _path("ASVA_shop.zip")
    ready = os.path.exists(p)
    size = f"{os.path.getsize(p) / 1e6:.0f} MB" if ready else ""
    btn = (f'<a class="dl" href="/download/ASVA_shop.zip">Download ASVA for Windows ({size})</a>'
           if ready else
           '<div class="soon">The download is being published. Please check back shortly.</div>')
    return HTMLResponse(f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download ASVA</title>
<style>
 body{{margin:0;background:#f7f5f0;color:#1c2620;font-family:'SF Pro Text',-apple-system,'Segoe UI',system-ui,sans-serif;line-height:1.6}}
 .wrap{{max-width:640px;margin:0 auto;padding:70px 22px}}
 .logo{{font-weight:800;letter-spacing:.14em}}.logo b{{color:#0a7d33}}
 h1{{font-family:'Iowan Old Style',Georgia,serif;font-size:2.2rem;letter-spacing:-.02em;margin:26px 0 8px}}
 .muted{{color:#5d6b62}}
 .card{{background:#fff;border:1px solid #e7e3da;border-radius:16px;padding:26px;margin:26px 0}}
 .ver{{display:inline-block;background:#eef4ee;color:#0a7d33;font-weight:700;font-size:.78rem;padding:4px 11px;border-radius:999px}}
 .dl{{display:inline-block;margin-top:14px;background:#0a7d33;color:#fff;font-weight:600;text-decoration:none;padding:13px 24px;border-radius:9px}}
 .dl:hover{{background:#0c8f3b}}
 .soon{{margin-top:12px;color:#956400;background:#fbf3db;border-radius:9px;padding:11px 14px;font-size:.92rem}}
 ol{{padding-left:20px}} li{{margin:6px 0}}
 .req{{font-size:.86rem;color:#5d6b62;margin-top:8px}}
 a.back{{color:#5d6b62;text-decoration:none}}
</style></head><body>
<div class="wrap">
  <div class="logo">AS<b>V</b>A</div>
  <h1>Download ASVA</h1>
  <p class="muted">The shop app for Windows. It reads your TallyPrime and sends bills and
     reminders on WhatsApp from your own number.</p>
  <div class="card">
    <span class="ver">Version {ver}</span>
    <div>{btn}</div>
    <div class="req">Windows 10 or 11, TallyPrime, and the licence details from your ASVA setup.</div>
  </div>
  <h3>Setting it up</h3>
  <ol>
    <li>Unzip the download to <b>C:\\ASVA</b>.</li>
    <li>Open <b>tally_agent\\config.json</b> and paste the <b>business id</b> and
        <b>agent token</b> you were given.</li>
    <li>Run <b>SETUP.bat</b> once, then <b>START.bat</b>.</li>
    <li>Scan the WhatsApp QR at <b>localhost:3001/qr</b> with the shop's phone.</li>
  </ol>
  <p class="muted">Don't have your licence details yet? Contact us and we will set you up.</p>
  <p><a class="back" href="/">&larr; Back to tryasva.com</a></p>
</div></body></html>""")
