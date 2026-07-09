@echo off
REM Opens the ASVA dashboard in a clean app-style window (no browser bar).
REM Double-click this instead of typing the localhost URL.
set TOKEN=9_E-A6kV2cYvjBh4JiTmGg0GAWNkYyUmUr-gSL_W-fQ
start msedge --app="http://localhost:8000/admin?token=%TOKEN%"
