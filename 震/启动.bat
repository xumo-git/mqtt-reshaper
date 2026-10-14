@echo off
rem ---------------------------------------------------------------
rem  KingIOServer MQTT payload reshaper - forwarder
rem
rem  Keep this file ASCII-only. cmd parses .bat with the ANSI codepage
rem  and non-ASCII text here breaks the script. All Chinese paths
rem  (the trigram folders) are resolved by launch.py next to this file.
rem ---------------------------------------------------------------
chcp 65001 >nul
title KingIOServer MQTT Reshaper
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>&1
if errorlevel 1 set PY=C:\Program Files\Python312\python.exe

if not exist "%~dp0launch.py" (
  echo [ERROR] launch.py not found next to this .bat
  pause
  exit /b 1
)

"%PY%" -I "%~dp0launch.py" reshaper

echo.
echo [stopped]
pause
