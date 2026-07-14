@echo off
REM ============================================================
REM  ASVA HOST - the always-on i3 laptop (server + bot)
REM  Two self-healing windows:
REM    [1] Backend + scheduler + Command Center (port 8000)
REM    [2] BOT WhatsApp - YOUR number (port 3002, owner-facing)
REM  The bot handles owner digests, alerts and commands. It does
REM  NOT message customers. The scheduler here decides WHEN to
REM  remind and queues the message; the SHOP laptop delivers it
REM  from the SHOP's own WhatsApp (that number is scanned on the
REM  shop's laptop, never here). Each window auto-restarts.
REM ============================================================
cd /d "%~dp0"

if "%1"=="backend" goto run_backend
if "%1"=="bot"     goto run_bot

if not exist ".env" goto no_env

echo ================================
echo   ASVA HOST - starting server
echo ================================
echo.
echo [1/2] Backend + scheduler + Command Center (port 8000)...
start "ASVA HOST - Backend" "%~f0" backend
echo       waiting for backend to come up...
timeout /t 8 /nobreak >nul

echo [2/2] BOT WhatsApp - your number (port 3002)...
start "ASVA HOST - WhatsApp (Bot)" "%~f0" bot
echo       first time only: open https://link.tryasva.com/qr (or
echo       http://localhost:3002/qr) and scan with YOUR phone.
echo.
echo Command Center:  http://localhost:8000/ops   (or api.tryasva.com/ops)
echo.
echo Leave these windows open. Close this launcher only.
timeout /t 6 /nobreak >nul
exit /b

REM ---------------- self-healing children ----------------
:run_backend
title ASVA HOST - Backend
:loop_backend
call .venv\Scripts\activate.bat 2>nul
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
echo.
echo [Backend stopped - restarting in 5s]  (Ctrl+C twice to quit)
timeout /t 5 /nobreak >nul
goto loop_backend

:run_bot
title ASVA HOST - WhatsApp (Bot)
cd /d "%~dp0wa_service"
set PORT=3002
set WA_CHANNEL=bot
set SESSION_ID=bot
set BACKEND_URL=http://localhost:8000
:loop_bot
call npm start
echo.
echo [Bot WhatsApp stopped - restarting in 6s]
timeout /t 6 /nobreak >nul
goto loop_bot

:no_env
echo .env not found. Copy your host .env into this folder first.
pause
