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

echo [2/3] Shop WhatsApp (port 3001)...
start "ASVA - WhatsApp (Shop)" "%~f0" wa
echo       giving WhatsApp time to load and connect...
timeout /t 22 /nobreak >nul

echo [3/3] Tally watcher...
start "ASVA - Tally Watcher" "%~f0" watch

echo.
echo Opening QR page (first-time linking only)...
start http://localhost:3001/qr

echo.
echo  Sab chalu! 3 windows khule rahenge - INHE BAND MAT KARO.
echo  - Shop QR   : localhost:3001/qr  (shop ka WhatsApp - bill/reminder)
echo  - Bill banao Tally mein  -^> customer ko WhatsApp 2 min mein
echo  - Reminders roz 11 baje (Digest aapke separate bot number par aayega)
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
REM Use the .venv Python built by SETUP.bat (3.11-3.13); fall back to bare python.
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
:loop_backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
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


REM ---------------- Tally watcher (auto-restart) ----------------
:run_watch
title ASVA - Tally Watcher
cd /d "%~dp0"
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
:loop_watch
"%PY%" -u tally_agent\agent.py --watch
echo.
echo Tally watcher stopped. Restarting in 10s... close window to stop.
timeout /t 10 /nobreak >nul
goto loop_watch
