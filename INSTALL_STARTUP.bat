@echo off
REM ============================================================
REM  ASVA - install auto-start (run ONCE on the host).
REM
REM  Registers ASVA to launch itself after every reboot or login,
REM  so the backend, WhatsApp, keep-awake and public tunnel come
REM  back on their own. Crashes are already handled by the restart
REM  loop inside ASVA_HOST.bat; THIS closes the reboot/sleep gap.
REM
REM  Double-click this file once. No admin needed for a per-user
REM  logon task. To remove, see the commands printed at the end.
REM ============================================================
cd /d "%~dp0"
setlocal
set ok=1

echo Registering ASVA to start on every login...
echo.

schtasks /create /tn "ASVA Host" /tr "\"%~dp0ASVA_HOST.bat\"" /sc onlogon /f
if errorlevel 1 set ok=0

if exist "%~dp0KEEP_AWAKE.bat" (
  schtasks /create /tn "ASVA Keep Awake" /tr "\"%~dp0KEEP_AWAKE.bat\"" /sc onlogon /f
  if errorlevel 1 set ok=0
)

if exist "%~dp0TUNNEL.bat" (
  schtasks /create /tn "ASVA Tunnel" /tr "\"%~dp0TUNNEL.bat\"" /sc onlogon /f
  if errorlevel 1 set ok=0
)

echo.
if "%ok%"=="1" (
  echo   Done. ASVA now starts automatically after a reboot or login.
  echo.
  echo   Tip: turn ON Windows auto-login so a reboot needs no one present:
  echo        run  netplwiz  , untick "Users must enter a password".
  echo.
  echo   To remove later:
  echo        schtasks /delete /tn "ASVA Host" /f
  echo        schtasks /delete /tn "ASVA Keep Awake" /f
  echo        schtasks /delete /tn "ASVA Tunnel" /f
) else (
  echo   Something did not register. Re-run this file as Administrator
  echo   ^(right-click -^> Run as administrator^).
)
echo.
pause
endlocal
