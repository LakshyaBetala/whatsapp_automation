@echo off
REM ============================================================
REM  WhatsApp Tally SaaS — daily start (double-click and forget)
REM  Starts: backend + WhatsApp sender + Tally watcher
REM ============================================================
cd /d "%~dp0"

if not exist ".env" (
    echo ERROR: .env missing — run from the full copied folder.
    pause & exit /b 1
)

echo Starting backend (window 1)...
start "Tally SaaS - Backend" cmd /k "cd /d "%~dp0" && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo Starting WhatsApp service (window 2)...
start "Tally SaaS - WhatsApp" cmd /k "cd /d "%~dp0wa_service" && node index.js"

echo Waiting for services...
timeout /t 12 /nobreak >nul

echo Starting Tally watcher (window 3)...
start "Tally SaaS - Watcher" cmd /k "cd /d "%~dp0TallyAgentRelease" && RishabTallyAgent.exe --watch"

echo Opening WhatsApp QR page (first time only: scan with shop WhatsApp)...
start http://localhost:3001/qr

echo.
echo  Sab chalu! 3 windows khule rahenge — INHE BAND MAT KARO.
echo  - Bill banao Tally mein -> customer ko WhatsApp 2 min mein
echo  - Reminders roz 11 baje | Digest raat 9 baje
echo.
pause
