@echo off
setlocal EnableExtensions
REM ==================================================================
REM  ASVA_BOT.bat  -  ONE resilient file: installs whatever is missing,
REM  repairs a broken setup, then runs the bot. Double-click any time.
REM
REM  It will, on its own:
REM    - copy .env.bot -> .env if needed
REM    - install Python 3.12 (winget) if no supported Python is found
REM    - build/repair the Python .venv and install packages
REM    - install Node.js LTS (winget) if Node is missing
REM    - install the WhatsApp service deps (Baileys engine - NO browser,
REM      NO Chromium download at all)
REM    - start backend + bot, open the QR, auto-restart on crash
REM ==================================================================
cd /d "%~dp0"

if "%1"=="backend" goto run_backend
if "%1"=="bot"     goto run_bot

echo ================================
echo   ASVA BOT - preparing...
echo ================================
echo.

REM --- 1. .env -------------------------------------------------------
if not exist ".env" if exist ".env.bot" copy /y ".env.bot" ".env" >nul
if not exist ".env" goto no_env

REM --- 2. Python (install if missing) -------------------------------
call :ensure_python
if not defined BOOTPY goto python_failed

REM --- 3. Python venv (build/repair) --------------------------------
call :ensure_venv
if not exist ".venv\Scripts\python.exe" goto venv_failed

REM --- 4. Node.js (install if missing) ------------------------------
call :ensure_node
if errorlevel 1 goto node_failed

REM --- 5. WhatsApp service deps (no Chromium download) --------------
call :ensure_wa

REM --- 6. Start everything ------------------------------------------
echo.
echo [1/2] Backend (port 8000)...
start "ASVA Bot - Backend" "%~f0" backend
echo       waiting for backend...
timeout /t 8 /nobreak >nul

echo [2/2] Bot WhatsApp (port 3001)...
start "ASVA Bot - WhatsApp" "%~f0" bot
echo       loading WhatsApp (Baileys - no browser, no download)...
timeout /t 18 /nobreak >nul

echo.
echo Opening QR page (first-time linking only)...
start http://localhost:3001/qr
echo.
echo  Bot chalu! Ye 2 windows khule rehne dein - BAND MAT KARO.
echo  - QR scan : localhost:3001/qr   (bot number se scan karo)
echo  - Health  : localhost:8000/health
echo.
pause
exit /b

REM ================= subroutines =================

:ensure_python
set "BOOTPY="
for %%V in (3.13 3.12 3.11) do call :probe_py %%V
if defined BOOTPY goto :eof
echo [install] Koi supported Python nahi mila - Python 3.12 install ho raha hai (winget)...
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
for %%V in (3.13 3.12 3.11) do call :probe_py %%V
goto :eof

:probe_py
if defined BOOTPY goto :eof
py -%1 -c "import sys" >nul 2>&1 && set "BOOTPY=py -%1"
goto :eof

:ensure_venv
if not exist ".venv\Scripts\python.exe" goto make_venv
".venv\Scripts\python.exe" -c "import fastapi,pydantic,supabase,uvicorn" >nul 2>&1
if not errorlevel 1 goto :eof
echo [repair] venv adhoora tha - dobara bana rahe hain...
rmdir /s /q ".venv"
:make_venv
echo [setup] Python venv ban raha hai (~2 min)...
%BOOTPY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto :eof
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not errorlevel 1 goto :eof
echo [retry] pip dobara try kar rahe hain...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
goto :eof

:ensure_node
node -e "process.exit(parseInt(process.versions.node)>=18?0:1)" >nul 2>&1
if not errorlevel 1 exit /b 0
echo [install] Node.js LTS install ho raha hai (winget)...
winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
REM Make node/npm usable in THIS window without a restart:
set "PATH=%PATH%;C:\Program Files\nodejs"
node -e "process.exit(parseInt(process.versions.node)>=18?0:1)" >nul 2>&1
if not errorlevel 1 exit /b 0
exit /b 1

:ensure_wa
REM Baileys engine (no browser, no Chromium). If it is not installed yet (e.g.
REM an older install that still has whatsapp-web.js), wipe node_modules and do a
REM clean install - this is fast because there is no Chromium download.
if exist "wa_service\node_modules\@whiskeysockets\baileys" goto :eof
echo [setup] WhatsApp engine (Baileys) install ho raha hai (no browser download)...
cd wa_service
if exist node_modules rmdir /s /q node_modules
call npm install --no-audit --no-fund
cd ..
goto :eof

REM ================= error exits =================
:no_env
echo ERROR: .env aur .env.bot dono missing. Supabase keys ke saath .env chahiye.
pause
exit /b 1
:python_failed
echo ERROR: Python install/detect fail. python.org se Python 3.12 install karke
echo is window ko band karke ASVA_BOT.bat dobara chalayein.
pause
exit /b 1
:venv_failed
echo ERROR: .venv nahi bana. Internet check karke dobara chalayein.
pause
exit /b 1
:node_failed
echo ERROR: Node.js install/detect fail. nodejs.org se install karke is window ko
echo band karke ASVA_BOT.bat dobara chalayein.
pause
exit /b 1

REM ================= service workers (auto-restart) =================
:run_backend
title ASVA Bot - Backend
set ENABLE_REMINDER_SWEEP=false
set ENABLE_SUBSCRIPTION_CHECK=false
set ENABLE_EOD_DIGEST=true
set SEND_VIA_OUTBOX=true
set ENABLE_OUTBOX_SEND=false
set OPENWA_URL=http://localhost:3001
set PLATFORM_WA_URL=
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
:loop_backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo.
echo Backend stopped/crashed. Restarting in 5s... close this window to stop.
timeout /t 5 /nobreak >nul
goto loop_backend

:run_bot
title ASVA Bot - WhatsApp
cd /d "%~dp0wa_service"
set PORT=3001
set SESSION_ID=bot
set WA_CHANNEL=bot
:loop_bot
node index.js
echo.
echo Bot WhatsApp stopped/crashed. Restarting in 5s... close window to stop.
timeout /t 5 /nobreak >nul
goto loop_bot
