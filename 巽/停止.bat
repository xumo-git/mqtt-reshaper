@echo off
rem ---------------------------------------------------------------
rem  Stop the background Web console
rem
rem  Keep this file ASCII-only. cmd parses .bat with the ANSI codepage
rem  and non-ASCII text here breaks the script. All Chinese output is
rem  printed by stop.py instead.
rem ---------------------------------------------------------------
chcp 65001 >nul
title MQTT Forwarder - Stop
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>&1
if errorlevel 1 set PY=C:\Program Files\Python312\python.exe

if not exist "%~dp0stop.py" (
  echo [ERROR] stop.py not found next to this .bat
  pause
  exit /b 1
)

"%PY%" -I "%~dp0stop.py"

echo.
pause
