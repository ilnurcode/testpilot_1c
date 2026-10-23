@echo off
setlocal
cd /d "%~dp0"
title Stop TestPilot 1C
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop CLI was not found.
  pause
  exit /b 1
)
docker compose down
if errorlevel 1 (
  echo Could not stop the TestPilot Compose project. Check Docker Desktop.
  pause
  exit /b 1
)
echo TestPilot stopped. Your project files and database volume are preserved.
timeout /t 5 /nobreak >nul
