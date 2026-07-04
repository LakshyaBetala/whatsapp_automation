@echo off
REM ── WhatsApp Tally SaaS — start everything on this laptop ────────────
cd /d "%~dp0"
start "WA Service" cmd /k start_wa_service.bat
timeout /t 3 /nobreak >nul
start "Backend" cmd /k start_backend.bat
echo Both services launching in their own windows.
echo   Backend : http://localhost:8000/health
echo   WhatsApp: http://localhost:3001/api/wa/status
