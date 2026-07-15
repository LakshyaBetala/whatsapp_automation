@echo off
REM ============================================================
REM  ASVA HOST - ONE file. Sets up the first time, then starts
REM  the whole server every time. Double-click and forget.
REM    [1] Backend + scheduler + Command Center + landing (8000)
REM    [2] Bot WhatsApp - YOUR number (3002)
REM  First run installs Python + Node dependencies (2-3 min).
REM  Also run TUNNEL.bat (for tryasva.com) and KEEP_AWAKE.bat once.
REM ============================================================
cd /d "%~dp0"

if "%1"=="backend" goto run_backend
if "%1"=="bot"     goto run_bot

if not exist ".env" goto no_env

REM ---------------- one-time setup (only if missing) ----------------
if not exist ".venv\Scripts\python.exe" (
  echo [setup] Creating Python environment, this takes a few minutes...
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
)
if not exist "wa_service\node_modules" (
  echo [setup] Installing WhatsApp service, this takes a few minutes...
  pushd wa_service & call npm install & popd
)

echo ================================
echo   ASVA HOST - starting server
echo ================================
echo.
echo [1/2] Backend + Command Center + landing (port 8000)...
start "ASVA HOST - Backend" "%~f0" backend
echo       waiting for backend to come up...
timeout /t 8 /nobreak >nul
echo [2/2] BOT WhatsApp - your number (port 3002)...
start "ASVA HOST - WhatsApp (Bot)" "%~f0" bot
echo.
echo  Landing:         https://tryasva.com   (after TUNNEL.bat)
echo  Command Center:  https://tryasva.com/ops
echo  Scan the bot:    https://link.tryasva.com/qr  (with YOUR phone)
echo.
echo Leave the two windows open. Close this launcher only.
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
echo .env not found. Unzip the whole ASVA_server.zip and run this from that folder.
pause
