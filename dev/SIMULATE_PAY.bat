@echo off
REM Live pitch demo: make a customer "report a payment". Prompts for an editable
REM number + amount, then it appears in the app's Payments tab in real time.
setlocal
set "GB=C:\Program Files\Git\bin\bash.exe"
if not exist "%GB%" set "GB=C:\Program Files\Git\usr\bin\bash.exe"
cd /d "%~dp0.."
set /p NUM=Customer WhatsApp number (e.g. 919812300003):
set /p AMT=Amount paid (e.g. 90000):
"%GB%" -lc "set -a; source dev/.env.dev; set +a; PY=.venv/Scripts/python; [ -f \"$PY\" ] || PY=.venv/bin/python; \"$PY\" dev/simulate_payment.py %NUM% %AMT%"
pause
