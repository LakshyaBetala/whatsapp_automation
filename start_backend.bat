@echo off
REM ── WhatsApp Tally SaaS — backend (FastAPI + scheduler) ──────────────
REM IMPORTANT: exactly ONE worker. The EOD/reminder scheduler runs
REM in-process; more workers = duplicate WhatsApp sends.
cd /d "%~dp0"

if not exist ".env" (
    echo ERROR: .env file missing. Copy .env.example to .env and fill in
    echo your Supabase keys first.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

echo Starting backend on http://0.0.0.0:8000 (LAN-accessible) ...
%PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
