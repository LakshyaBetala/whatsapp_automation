@echo off
REM ============================================================
REM  ASVA HOST - the always-on i3 laptop (the server)
REM  Two self-healing windows:
REM    [1] Backend + scheduler + Command Center (port 8000)
REM    [2] Shop WhatsApp session (port 3001)
REM  The shop's WhatsApp lives HERE so reminders send even on
REM  Sundays when the shop's own laptop is off. Each window
REM  auto-restarts its service if it ever crashes.
REM
REM  The shop laptops run only the Tally agent (AGENT_ONLY.bat)
REM  and point their backend_url at this host's tunnel URL.
REM ============================================================
cd /d "%~dp0"

if "%1"=="backend" goto run_backend
if "%1"=="wa"      goto run_wa

if not exist ".env" goto no_env

echo ================================
echo   ASVA HOST - starting server
echo ================================
echo.
echo [1/2] Backend + scheduler + Command Center (port 8000)...
start "ASVA HOST - Backend" "%~f0" backend
echo       waiting for backend to come up...
timeout /t 8 /nobreak >nul

echo [2/2] Shop WhatsApp session (port 3001)...
start "ASVA HOST - WhatsApp (Shop)" "%~f0" wa
echo       first time only: open http://localhost:3001/qr and scan
echo       the SHOP owner's WhatsApp to link it here.
echo.
echo Command Center:  http://localhost:8000/ops
echo (from anywhere:  your Cloudflare tunnel URL + /ops )
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

:run_wa
title ASVA HOST - WhatsApp (Shop)
cd /d "%~dp0wa_service"
:loop_wa
call npm start
echo.
echo [WhatsApp service stopped - restarting in 6s]
timeout /t 6 /nobreak >nul
goto loop_wa

:no_env
echo .env not found. Copy your host .env into this folder first.
pause
