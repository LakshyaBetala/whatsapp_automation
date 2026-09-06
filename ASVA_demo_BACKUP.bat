@echo off
REM ===================================================================
REM  ASVA demo BACKUP
REM  Saves this laptop's ASVA identity (which shop it is paired to and
REM  its WhatsApp login) so you can pair it to a demo shop for a video
REM  and then put the real shop back exactly as it was.
REM
REM  CLOSE ASVA COMPLETELY BEFORE RUNNING THIS (check the tray icon).
REM ===================================================================
setlocal
title ASVA demo backup

set "SRC="
for %%D in ("%APPDATA%\ASVA" "%APPDATA%\asva-desktop" "%APPDATA%\Asva") do (
  if exist "%%~D\config.json" set "SRC=%%~D"
)
if not defined SRC (
  echo.
  echo  Could not find an ASVA configuration on this laptop.
  echo  Looked in: %APPDATA%\ASVA , asva-desktop , Asva
  echo.
  pause & exit /b 1
)

for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set "STAMP=%%c%%b%%a"
set "DEST=%USERPROFILE%\Desktop\ASVA_backup_%STAMP%"
if exist "%DEST%" set "DEST=%DEST%_%RANDOM%"

echo.
echo  Found ASVA at : %SRC%
echo  Backing up to : %DEST%
echo.
mkdir "%DEST%" 2>nul
copy /y "%SRC%\config.json" "%DEST%\config.json" >nul
if exist "%SRC%\wa" xcopy /e /i /y /q "%SRC%\wa" "%DEST%\wa" >nul

if exist "%DEST%\config.json" (
  echo  DONE. Your real shop setup is saved.
  echo.
  echo  Keep the folder  %DEST%  safe.
  echo  After the video, run ASVA_demo_RESTORE.bat to put it back.
) else (
  echo  BACKUP FAILED. Do not continue with the demo.
)
echo.
pause
