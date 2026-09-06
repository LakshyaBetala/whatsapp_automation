@echo off
REM ===================================================================
REM  ASVA demo RESTORE
REM  Puts the real shop's ASVA identity back after a demo recording.
REM  Drag the backup folder onto this file, or run it and paste the path.
REM
REM  CLOSE ASVA COMPLETELY BEFORE RUNNING THIS (check the tray icon).
REM ===================================================================
setlocal
title ASVA demo restore

set "BK=%~1"
if not defined BK set /p "BK=Paste the backup folder path and press Enter: "
set "BK=%BK:"=%"

if not exist "%BK%\config.json" (
  echo.
  echo  That folder does not contain a config.json - wrong folder?
  echo  Given: %BK%
  echo.
  pause & exit /b 1
)

set "DST="
for %%D in ("%APPDATA%\ASVA" "%APPDATA%\asva-desktop" "%APPDATA%\Asva") do (
  if exist "%%~D\." set "DST=%%~D"
)
if not defined DST set "DST=%APPDATA%\ASVA"
mkdir "%DST%" 2>nul

echo.
echo  Restoring from : %BK%
echo  Into           : %DST%
echo.
copy /y "%BK%\config.json" "%DST%\config.json" >nul
if exist "%BK%\wa" (
  if exist "%DST%\wa" rmdir /s /q "%DST%\wa"
  xcopy /e /i /y /q "%BK%\wa" "%DST%\wa" >nul
)

echo  DONE. Open ASVA - it is back on the real shop.
echo.
echo  If the shop WhatsApp asks to link again, open WhatsApp Setup in
echo  ASVA and scan once. Your bills and customers are safe in the
echo  cloud either way, nothing is lost.
echo.
pause
