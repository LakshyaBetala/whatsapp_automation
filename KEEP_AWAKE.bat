@echo off
REM ============================================================
REM  ASVA HOST - never sleep / never hibernate (run once, as admin)
REM  An always-on server must not sleep or the scheduler + WhatsApp
REM  stop. This sets the power plan so the host stays up with the
REM  lid closed and only the screen turns off.
REM  Right-click -> Run as administrator.
REM ============================================================
echo Setting this laptop to stay awake 24/7...
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 10
powercfg /change disk-timeout-ac 0
REM Keep running with the lid closed (plugged in):
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
echo Done. The host will no longer sleep or hibernate.
echo Keep it plugged in. Screen may turn off after 10 min - that is fine.
pause
