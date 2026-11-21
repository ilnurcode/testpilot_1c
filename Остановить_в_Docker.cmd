@echo off
chcp 65001 >nul
setlocal
title Остановка TestPilot 1C (Docker)
cd /d "%~dp0"

rem Останавливаются только контейнеры Docker-профиля, через штатную обёртку
rem scripts\compose.ps1 stop. Тома и данные не удаляются.
if not exist "scripts\compose.ps1" (
    echo [ОШИБКА] Нет scripts\compose.ps1: остановка Docker-профиля не выполняется.
    echo Никакая другая операция не запускалась.
    pause
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\compose.ps1" stop
if errorlevel 1 (
    echo [ОШИБКА] Не удалось остановить контейнеры. Смотрите сообщение выше.
    pause
    exit /b 1
)
echo Контейнеры TestPilot 1C остановлены. Тома, .env и данные сохранены.
timeout /t 3 >nul
exit /b 0
