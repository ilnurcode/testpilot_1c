@echo off
chcp 65001 >nul
setlocal
title Запуск TestPilot 1C (Docker)
cd /d "%~dp0"

rem Docker-профиль. Единственный вход — scripts\first-run.ps1 -Docker (A3):
rem db → официальное чтение схемы в образе → init только для uninitialized →
rem отказ при pending/ahead → start-docker.ps1 (фактический /health, receipt, браузер).
rem Python, .venv и .env в Docker-режиме не нужны.
rem Каталог данных: -Packaged даёт %LOCALAPPDATA%\TestPilot1C (для установки в
rem Program Files); переменная TP_DATA_DIR перед запуском переопределяет путь.
set "FIRSTRUN=scripts\first-run.ps1"
if not exist "%FIRSTRUN%" (
    echo [ОШИБКА] Не найден %FIRSTRUN%: пакет неполный.
    echo Docker-запуск не выполняется; native-запуск вместо Docker не подставляется.
    pause
    exit /b 2
)

set "PACKAGED="
if exist "%~dp0manifest.json" set "PACKAGED=-Packaged"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%FIRSTRUN%" -Docker %PACKAGED%
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" exit /b 0

echo.
echo НЕ ГОТОВО: Docker-запуск завершился с кодом %CODE%. Сервис не запущен.
if "%CODE%"=="2" (
    echo   Нет предпосылок или пакет неполный: Docker Desktop CLI, права записи
    echo   в каталог данных либо отсутствует дочерний скрипт. Профиль не подменялся.
)
if "%CODE%"=="3" (
    echo   Схема БД не на голове миграций. Сначала проверенный backup, затем:
    echo     powershell -File scripts\update.ps1 -Confirmed -Docker
)
pause
exit /b %CODE%
