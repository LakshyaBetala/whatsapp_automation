@echo off
REM ============================================================
REM  ASVA — daily start (double-click and forget)
REM  Starts: backend + WhatsApp + company bot + Tally watcher
REM ============================================================
cd /d "%~dp0"

if not exist ".env" (
    echo ERROR: .env missing — run from the full copied folder.
    pause & exit /b 1
)

echo Starting backend (window 1)...
start "ASVA - Backend" cmd /k "cd /d "%~dp0" && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo Starting WhatsApp service (window 2)...
start "ASVA - WhatsApp" cmd /k "cd /d "%~dp0wa_service" && node index.js"

echo Starting company-number bot (window 3)...
start "ASVA - Company Bot" cmd /k "cd /d "%~dp0wa_service" && set PORT=3002&& set SESSION_ID=platform&& node index.js"

echo Waiting for services...
timeout /t 12 /nobreak >nul

echo Starting Tally watcher (window 4)...
start "ASVA - Tally Watcher" cmd /k "cd /d "%~dp0Asva" && Asva.exe --watch"

echo Opening QR pages (first time only)...
echo   - localhost:3001/qr : scan with SHOP WhatsApp
echo   - localhost:3002/qr : scan with COMPANY number (9344110272)
start http://localhost:3001/qr
start http://localhost:3002/qr

echo.
echo  Sab chalu! 4 windows khule rahenge — INHE BAND MAT KARO.
echo  - Bill banao Tally mein -> customer ko WhatsApp 2 min mein
echo  - Reminders roz 11 baje | Digest raat 9 baje
echo.
pause
