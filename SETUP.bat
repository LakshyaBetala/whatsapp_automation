@echo off
setlocal
title ASVA Setup
cd /d "%~dp0"
echo.
echo ================================
echo   ASVA - ONE-TIME SETUP
echo ================================
echo.

echo [1/5] Checking Python 3.10+ ...
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 goto install_python
echo       Python OK.
goto check_node

:install_python
echo       Python missing or too old. Installing via winget, 2-3 min...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto winget_failed
echo.
echo   *** Python installed. CLOSE this window and run SETUP.bat AGAIN. ***
echo   *** Windows needs a fresh window to find the new Python.         ***
goto end

:check_node
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
echo [3/5] Installing Python packages, 2-3 min...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pip_failed
echo       Python packages OK.

echo [4/5] Installing WhatsApp service packages, 3-5 min...
cd wa_service
if exist node_modules rmdir /s /q node_modules
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

:winget_failed
echo.
echo   ERROR: winget install failed. Internet check karein, phir
echo   SETUP.bat dobara chalayein. Agar phir bhi fail ho:
echo   python.org/downloads aur nodejs.org se manually install karein.
goto end

:pip_failed
echo.
echo   ERROR: pip install failed. Internet check karke dobara chalayein.
goto end

:end
echo.
pause
