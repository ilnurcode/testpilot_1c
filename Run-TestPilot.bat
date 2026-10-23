@echo off
setlocal
cd /d "%~dp0"
title TestPilot 1C - first start

where docker >nul 2>nul
if errorlevel 1 goto no_docker

docker info --format "{{.OSType}}" 2>nul | findstr /x "linux" >nul
if errorlevel 1 goto start_docker
goto launch

:start_docker
if not exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" goto docker_not_ready
echo Starting Docker Desktop. The first start may take a few minutes...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a attempts=0
:wait_docker
set /a attempts+=1
docker info --format "{{.OSType}}" 2>nul | findstr /x "linux" >nul
if not errorlevel 1 goto launch
if %attempts% GEQ 120 goto docker_not_ready
timeout /t 2 /nobreak >nul
goto wait_docker

:launch
echo Building and starting TestPilot containers. The first build can take several minutes.
echo Local-only demo login: expert / TestPilotLocal123! unless .env overrides it.
docker compose up --build --detach --wait --wait-timeout 900 impact intelligence web
if errorlevel 1 goto compose_failed
echo TestPilot is ready: http://localhost:8000
start "" "http://localhost:8000"
echo Keep Docker Desktop running while you use TestPilot.
timeout /t 8 /nobreak >nul
exit /b 0

:no_docker
echo Docker Desktop was not found. Install Docker Desktop for Windows and try again.
start "" "https://docs.docker.com/desktop/setup/install/windows-install/"
pause
exit /b 1

:docker_not_ready
echo Docker Desktop did not become ready in Linux containers mode.
echo Start or repair Docker Desktop, confirm Linux containers, then double-click this file again.
pause
exit /b 1

:compose_failed
echo TestPilot failed to start. Container status and recent logs follow.
docker compose ps
docker compose logs --tail=60
pause
exit /b 1
