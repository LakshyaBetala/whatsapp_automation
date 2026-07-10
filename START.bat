@echo off
REM ============================================================
REM  ASVA - daily start (double-click and forget)
REM  One file, 4 self-healing windows:
REM    backend + Shop WhatsApp (3001) + ASVA Bot (3002) + Tally watcher
REM  Each window auto-restarts its service if it ever crashes.
REM  TWO numbers:
REM    3001 = shop's own number  -> bills, reminders, customer replies
REM    3002 = ASVA bot number    -> owner-only assistant (LIST/BILL/photo/digest)
REM ============================================================
cd /d "%~dp0"

REM --- child dispatch: START.bat re-launches itself per service ---
if "%1"=="backend" goto run_backend
if "%1"=="wa"      goto run_wa
if "%1"=="bot"     goto run_bot
if "%1"=="watch"   goto run_watch

REM ---------------- launcher ----------------
if not exist ".env" goto no_env

echo ================================
echo   ASVA - starting all services
echo ================================
echo.

echo [1/3] Backend (port 8000)...
start "ASVA - Backend" "%~f0" backend
echo       waiting for backend to come up...
timeout /t 8 /nobreak >nul

echo [2/4] Shop WhatsApp (port 3001)...
start "ASVA - WhatsApp (Shop)" "%~f0" wa
echo       giving WhatsApp time to load and connect...
timeout /t 22 /nobreak >nul

echo [3/4] ASVA Bot WhatsApp (port 3002)...
start "ASVA - WhatsApp (Bot)" "%~f0" bot
echo       giving the bot number time to load and connect...
timeout /t 22 /nobreak >nul

echo [4/4] Tally watcher...
start "ASVA - Tally Watcher" "%~f0" watch

echo.
echo Opening QR pages (first-time linking only)...
start http://localhost:3001/qr
start http://localhost:3002/qr

echo.
echo  Sab chalu! 4 windows khule rahenge - INHE BAND MAT KARO.
echo  - Shop QR   : localhost:3001/qr  (shop ka WhatsApp - bill/reminder)
echo  - Bot QR    : localhost:3002/qr  (ASVA bot - owner assistant/digest)
echo  - Bill banao Tally mein  -^> customer ko WhatsApp 2 min mein
echo  - Reminders roz 11 baje  ^| Digest raat 10 baje
echo.
pause
exit /b

:no_env
echo ERROR: .env missing - run from the full copied ASVA folder.
pause
exit /b 1

REM ---------------- backend (auto-restart) ----------------
:run_backend
title ASVA - Backend
:loop_backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo.
echo Backend stopped/crashed. Restarting in 5s... close this window to stop.
timeout /t 5 /nobreak >nul
goto loop_backend

REM ---------------- Shop WhatsApp (auto-restart) ----------------
:run_wa
title ASVA - WhatsApp (Shop)
cd /d "%~dp0wa_service"
:loop_wa
node index.js
echo.
echo WhatsApp service stopped/crashed. Restarting in 5s... close window to stop.
timeout /t 5 /nobreak >nul
goto loop_wa

REM ---------------- ASVA Bot WhatsApp 3002 (auto-restart) ----------------
:run_bot
title ASVA - WhatsApp (Bot 3002)
cd /d "%~dp0wa_service"
set PORT=3002
set SESSION_ID=bot
set WA_CHANNEL=bot
:loop_bot
node index.js
echo.
echo Bot WhatsApp stopped/crashed. Restarting in 5s... close window to stop.
timeout /t 5 /nobreak >nul
goto loop_bot

REM ---------------- Tally watcher (auto-restart) ----------------
:run_watch
title ASVA - Tally Watcher
cd /d "%~dp0"
:loop_watch
python -u tally_agent\agent.py --watch
echo.
echo Tally watcher stopped. Restarting in 10s... close window to stop.
timeout /t 10 /nobreak >nul
goto loop_watch
