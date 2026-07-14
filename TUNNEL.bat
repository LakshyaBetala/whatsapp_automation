@echo off
REM ============================================================
REM  ASVA HOST - Cloudflare Tunnel (gives the host a public URL)
REM  Run this AFTER one-time setup in HOST_SETUP.md:
REM    1) cloudflared tunnel login
REM    2) cloudflared tunnel create asva
REM    3) cloudflared tunnel route dns asva asva.YOURDOMAIN.com
REM    4) put the config.yml where cloudflared expects it
REM  Then this window keeps the tunnel up and auto-restarts it.
REM
REM  No domain yet? For a quick TEST url (changes every restart):
REM    cloudflared tunnel --url http://localhost:8000
REM ============================================================
title ASVA HOST - Cloudflare Tunnel
:loop
cloudflared tunnel run asva
echo.
echo [Tunnel dropped - reconnecting in 5s]
timeout /t 5 /nobreak >nul
goto loop
