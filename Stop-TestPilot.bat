@echo off
setlocal
cd /d "%~dp0"
title Stop TestPilot 1C

rem Canonical native stop only. Docker containers are stopped by
rem "Остановить_в_Docker.cmd" (scripts\compose.ps1 stop): a different profile
rem is never touched implicitly.
if not exist "scripts\stop.ps1" (
  echo TestPilot package is incomplete: scripts\stop.ps1 is missing.
  echo No stop action was performed.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\stop.ps1"
if errorlevel 1 (
  echo Could not stop TestPilot. See the message above.
  pause
  exit /b 1
)
echo TestPilot stopped. Files, .env and the database volume are preserved.
echo Docker profile: use "Остановить_в_Docker.cmd" if containers were started.
timeout /t 5 /nobreak >nul
exit /b 0
