@echo off
rem ---------------------------------------------------------------
rem  Run status check
rem
rem  Keep this file ASCII-only. cmd parses .bat with the ANSI codepage
rem  and non-ASCII text here breaks the script. All Chinese output is
rem  printed by status.py instead.
rem ---------------------------------------------------------------
chcp 65001 >nul
title MQTT Forwarder - Status
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>&1
if errorlevel 1 set PY=C:\Program Files\Python312\python.exe

if not exist "%~dp0status.py" (
  echo [ERROR] status.py not found next to this .bat
  pause
  exit /b 1
)

"%PY%" -I "%~dp0status.py"

echo.
pause
