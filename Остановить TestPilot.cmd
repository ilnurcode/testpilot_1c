@echo off
title Остановка TestPilot 1C
chcp 65001 >nul
cd /d "%~dp0"
echo Остановка сервисов TestPilot 1C...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
echo.
echo Сервисы остановлены. База данных и файлы сохранены.
timeout /t 3 >nul
