@echo off
chcp 65001 >nul
setlocal
title TestPilot 1C Launcher
cd /d "%~dp0"

rem Единая точка запуска: scripts\first-run.ps1 (окружение → схема → запуск).
rem Пакетная установка кладёт данные и venv в TP_DATA_DIR (по умолчанию
rem %LOCALAPPDATA%\TestPilot1C), поэтому своего поиска .venv здесь нет.
set "FIRSTRUN=scripts\first-run.ps1"
if not exist "%FIRSTRUN%" (
    echo [ОШИБКА] Не найден %FIRSTRUN%: пакет неполный.
    echo Распакуйте пакет полностью или соберите его заново.
    pause
    exit /b 2
)

set "PACKAGED="
if exist "%~dp0manifest.json" set "PACKAGED=-Packaged"
if /I "%~1"=="--source" set "PACKAGED="
if /I "%~1"=="--packaged" set "PACKAGED=-Packaged"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%FIRSTRUN%" %PACKAGED%
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo НЕ ГОТОВО: первый запуск завершился с кодом %CODE%.
    echo Сервис не считается запущенным: смотрите отчёт выше.
    pause
)
exit /b %CODE%
