@echo off
chcp 65001 >nul
setlocal
title Остановка TestPilot 1C
cd /d "%~dp0"

if not exist "scripts\stop.ps1" (
    echo [ОШИБКА] Нет scripts\stop.ps1: остановка не выполнялась.
    pause
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\stop.ps1"
if errorlevel 1 (
    echo [ОШИБКА] Остановить сервисы не удалось. Смотрите сообщение выше.
    pause
    exit /b 1
)
echo Сервисы остановлены. База данных, .env и файлы сохранены.
echo Docker-профиль останавливается отдельно: Остановить_в_Docker.cmd.
timeout /t 3 >nul
exit /b 0
