@echo off
REM Launch the REAL ASVA desktop app (the exact .exe UI) against the LOCAL dev
REM backend - for recording an explanatory video with no Tally and no prod risk.
REM
REM  Order:
REM   1. Start Docker Desktop.
REM   2. Run dev\RUN_DEV.bat   (starts the dev DB + backend on :8000)  [keep it open]
REM   3. Run dev\SEED_DEV.bat  (loads sample customers/bills/payments) [once]
REM   4. Run THIS file         (opens the app; spawns its own WhatsApp service :3001)
REM
REM It uses a dev-only config (dev\app-config.json) via ASVA_DEV_CONFIG, so your
REM real ASVA pairing and WhatsApp login are never touched.
setlocal
cd /d "%~dp0.."
REM Make sure the dev backend is up before opening the app.
docker ps >nul 2>&1 || ( echo Docker is not running - start Docker Desktop first. & pause & exit /b 1 )
curl -s http://localhost:8000/health >nul 2>&1 || ( echo Dev backend not answering on :8000 - run dev\RUN_DEV.bat first and wait for it to say the backend is up. & pause & exit /b 1 )
set "ASVA_DEV_CONFIG=%~dp0app-config.json"
REM Make sure Electron runs as a GUI app, not in Node mode (that flag breaks it).
set "ELECTRON_RUN_AS_NODE="
echo Launching the ASVA app against the DEV backend (config: %ASVA_DEV_CONFIG%)...
cd desktop
npm start
pause
