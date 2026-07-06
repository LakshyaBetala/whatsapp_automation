@echo off
REM ============================================================
REM  WhatsApp Tally SaaS — ONE-TIME setup (run once per laptop)
REM  Needs: this folder copied to the laptop (WITH the .env file)
REM ============================================================
cd /d "%~dp0"
echo.
echo [1/4] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   Installing Python via winget ^(2-3 min^)...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   *** Python installed. CLOSE this window and run SETUP.bat AGAIN. ***
    pause
    exit /b
)

echo [2/4] Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo   Installing Node.js via winget ^(2-3 min^)...
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    echo.
    echo   *** Node installed. CLOSE this window and run SETUP.bat AGAIN. ***
    pause
    exit /b
)

if not exist ".env" (
    echo.
    echo   ERROR: .env file missing! Copy the FULL folder from the main
    echo   laptop ^(the .env has the database keys and is not on GitHub^).
    pause
    exit /b 1
)

echo [3/4] Installing Python packages ^(2-3 min^)...
python -m pip install -r requirements.txt --quiet

echo [4/4] Installing WhatsApp service packages ^(3-5 min^)...
cd wa_service
call npm install --no-audit --no-fund
cd ..

echo.
echo ============================================
echo  SETUP COMPLETE. Ab roz START.bat chalao.
echo ============================================
pause
