@echo off
REM One-click ASVA dev runner (uses Git Bash, not WSL). Start Docker Desktop first.
setlocal
set "GB=C:\Program Files\Git\bin\bash.exe"
if not exist "%GB%" set "GB=C:\Program Files\Git\usr\bin\bash.exe"
if not exist "%GB%" ( echo Git Bash not found. Install Git for Windows. & pause & exit /b 1 )
cd /d "%~dp0.."
echo Checking Docker...
docker ps >nul 2>&1 || ( echo Docker is not running - start Docker Desktop, wait ~1 min, then re-run this. & pause & exit /b 1 )
"%GB%" dev/run_dev.sh up
pause
