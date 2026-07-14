@echo off
REM ============================================================
REM  ASVA SHOP AGENT (thin client) - runs on the SHOP's laptop
REM  This laptop ONLY reads Tally and pushes it to the ASVA host.
REM  No backend, no WhatsApp, no database here. Everything else
REM  (reminders, digest, sending, Command Center) runs on the host.
REM
REM  Before first run, edit config.json:
REM    "backend_url": "https://asva.YOURDOMAIN.com"   (the host tunnel)
REM    "agent_token": "<the token from Add Business in the host>"
REM    "business_id": "<from Add Business>"
REM    "company_name": "<your Tally company>"
REM  Keep Tally open. This window auto-restarts the agent if it stops.
REM ============================================================
cd /d "%~dp0"
title ASVA Shop Agent (Tally -> ASVA host)
:loop
Asva\Asva.exe --watch
echo.
echo [Agent stopped - restarting in 8s]  (keep Tally open)
timeout /t 8 /nobreak >nul
goto loop
