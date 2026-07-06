@echo off
REM ============================================================
REM  ASVA — ONE-TIME setup (run once per laptop)
REM  Needs: this folder copied to the laptop (WITH the .env file)
REM ============================================================
cd /d "%~dp0"
echo.

echo [1/4] Checking Python (3.10+ required)...
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 (
    echo   Python missing or too old. Installing via winget (2-3 min)...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   *** Python installed. CLOSE this window and run SETUP.bat AGAIN. ***
    echo   *** (Windows needs a fresh window to find the new Python.)      ***
    pause
    exit /b
)
echo   Python OK.

echo [2/4] Checking Node.js (18+ required — old Node crashes WhatsApp)...
node -e "process.exit(parseInt(process.versions.node)>=18?0:1)" >nul 2>&1
if errorlevel 1 (
    echo   Node.js missing or TOO OLD. Installing latest via winget (2-3 min)...
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    echo.
    echo   *** Node installed. CLOSE this window and run SETUP.bat AGAIN. ***
    echo   *** If it still says too old: uninstall the old Node.js from   ***
    echo   *** Settings ^> Apps first, then rerun.                         ***
    pause
    exit /b
)
echo   Node.js OK.

if not exist ".env" (
    echo.
    echo   ERROR: .env file missing! Copy the FULL folder from the main
    echo   laptop (the .env has the database keys and is not on GitHub).
    pause
    exit /b 1
)

echo [3/4] Installing Python packages (2-3 min)...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo   ERROR: pip install failed. Check internet and rerun.
    pause
    exit /b 1
)

echo [4/4] Installing WhatsApp service packages (3-5 min)...
cd wa_service
if exist node_modules rmdir /s /q node_modules
call npm install --no-audit --no-fund
cd ..

echo.
echo ============================================
echo  ASVA SETUP COMPLETE. Ab roz START.bat chalao.
echo ============================================
pause
