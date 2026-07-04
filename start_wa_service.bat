@echo off
REM ── WhatsApp Tally SaaS — WhatsApp sender/receiver (Node) ────────────
REM First run shows a QR code via http://localhost:3001/api/wa/status
REM (or watch this window) — scan it with the business WhatsApp once.
cd /d "%~dp0wa_service"

if not exist "node_modules" (
    echo node_modules missing — running npm install first...
    call npm install
)

echo Starting WhatsApp service on port 3001 ...
node index.js
pause
