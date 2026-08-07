@echo off
REM Load sample data into the dev app (run AFTER RUN_DEV.bat is up in another window).
setlocal
set "GB=C:\Program Files\Git\bin\bash.exe"
if not exist "%GB%" set "GB=C:\Program Files\Git\usr\bin\bash.exe"
cd /d "%~dp0.."
"%GB%" dev/run_dev.sh seed
pause
