@echo off
rem ---------------------------------------------------------------
rem  KingIOServer MQTT reshaper - Web console
rem
rem  Keep this file ASCII-only. cmd parses .bat with the ANSI codepage
rem  and non-ASCII text here breaks the script. All Chinese paths
rem  (the trigram folders) are resolved by launch.py next to this file.
rem ---------------------------------------------------------------
chcp 65001 >nul
title KingIOServer MQTT Reshaper - Web UI
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>&1
if errorlevel 1 set PY=C:\Program Files\Python312\python.exe

if not exist "%~dp0launch.py" (
  echo [ERROR] launch.py not found next to this .bat
  pause
  exit /b 1
)

rem run detached: no black window stays open. the service keeps running even
rem after this window closes. stop it with stop.bat (the Chinese-named bat)
rem only this file *contents* must stay ASCII).
"%PY%" -I "%~dp0launch.py" webui 8090 --bg

echo.
echo [done]
pause
