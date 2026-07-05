@echo off
REM ── OPTIONAL second WhatsApp session: the COMPANY/platform number ────
REM This number talks to business OWNERS (9 PM digest, alerts, renewal
REM notices). Scan its QR at http://localhost:3002/qr with the company
REM WhatsApp. Then set in .env:  PLATFORM_WA_URL=http://localhost:3002
cd /d "%~dp0wa_service"

if not exist "node_modules" (
    echo node_modules missing — running npm install first...
    call npm install
)

set PORT=3002
set SESSION_ID=platform
echo Starting PLATFORM WhatsApp service on port 3002 ...
node index.js
pause
