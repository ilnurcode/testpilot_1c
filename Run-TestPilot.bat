@echo off
setlocal
cd /d "%~dp0"

rem Single canonical entry: scripts\first-run.ps1 (environment -> schema -> start).
rem Packaged installs keep data and venv in TP_DATA_DIR, so no .venv is expected here.
if not exist "scripts\first-run.ps1" (
  echo TestPilot package is incomplete: scripts\first-run.ps1 is missing.
  pause
  exit /b 2
)

set "PACKAGED="
if exist "%~dp0manifest.json" set "PACKAGED=-Packaged"
if /I "%~1"=="--source" set "PACKAGED="
if /I "%~1"=="--packaged" set "PACKAGED=-Packaged"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\first-run.ps1" %PACKAGED%
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo NOT READY: first run exited with code %CODE%. The service is not started.
  echo Environment, database schema and startup are reported above.
  pause
)
exit /b %CODE%
