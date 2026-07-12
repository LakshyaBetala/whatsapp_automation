@echo off
setlocal
title ASVA Setup
cd /d "%~dp0"
echo.
echo ================================
echo   ASVA - ONE-TIME SETUP
echo ================================
echo.

REM --- Pick a Python that has prebuilt wheels (3.11 / 3.12 / 3.13). ----------
REM Python 3.14 is too new: pydantic-core has no 3.14 wheel yet, so pip would
REM try to COMPILE it from Rust and fail without Visual Studio's linker. If no
REM supported Python is found we install 3.12 via winget. Everything installs
REM into an isolated .venv so a bare 3.14 on the machine does not interfere.
echo [1/5] Finding a supported Python (3.11-3.13)...
set "BOOTPY="
for %%V in (3.13 3.12 3.11) do call :try_py %%V
if defined BOOTPY goto have_py
echo       Not found. Installing Python 3.12 via winget, 2-3 min...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto winget_failed
echo.
echo   *** Python installed. CLOSE this window and run SETUP.bat AGAIN. ***
echo   *** Windows needs a fresh window to find the new Python.         ***
goto end

:have_py
echo       Using %BOOTPY%.

echo [2/5] Checking Node.js 18+ ...
node -e "process.exit(parseInt(process.versions.node)>=18?0:1)" >nul 2>&1
if errorlevel 1 goto install_node
echo       Node.js OK.
goto check_env

:install_node
echo       Node.js missing or TOO OLD. Installing latest via winget, 2-3 min...
winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto winget_failed
echo.
echo   *** Node installed. CLOSE this window and run SETUP.bat AGAIN.  ***
echo   *** If it still says too old: uninstall old Node.js from        ***
echo   *** Settings, Apps list first, then rerun SETUP.bat.            ***
goto end

:check_env
if exist ".env" goto pip_install
echo.
echo   ERROR: .env file missing!
echo   Copy the FULL ASVA folder from the main laptop.
goto end

:pip_install
echo [3/5] Creating .venv and installing Python packages, 2-3 min...
if exist ".venv" rmdir /s /q ".venv"
%BOOTPY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto venv_failed
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pip_failed
echo       Python packages OK.

echo [4/5] Installing WhatsApp service packages, 3-5 min...
cd wa_service
if exist node_modules rmdir /s /q node_modules
REM Do NOT download Chromium (it fails on many networks). The service uses the
REM Microsoft Edge already on Windows instead.
set PUPPETEER_SKIP_DOWNLOAD=1
set PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1
call npm install --no-audit --no-fund
cd ..

echo [5/5] Installing ASVA app (Electron), 3-5 min...
cd desktop
if exist node_modules rmdir /s /q node_modules
call npm install --no-audit --no-fund
cd ..
echo.
echo ==============================================
echo   ASVA SETUP COMPLETE.
echo   Ab roz ASVA.vbs kholo (ek hi app - sab kuch usmein).
echo ==============================================
goto end

REM ---- subroutine: set BOOTPY to the first Python that exists ----
:try_py
if defined BOOTPY goto :eof
py -%1 -c "import sys" >nul 2>&1 && set "BOOTPY=py -%1"
goto :eof

:winget_failed
echo.
echo   ERROR: winget install failed. Internet check karein, phir
echo   SETUP.bat dobara chalayein. Agar phir bhi fail ho:
echo   python.org/downloads aur nodejs.org se manually install karein.
goto end

:venv_failed
echo   ERROR: .venv nahi ban paya (%BOOTPY%).
goto end

:pip_failed
echo.
echo   ERROR: pip install failed. Internet check karke dobara chalayein.
goto end

:end
echo.
pause
