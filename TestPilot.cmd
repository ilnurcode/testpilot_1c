@echo off
title TestPilot 1C Launcher
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py
) else (
    echo [ОШИБКА] Виртуальное окружение .venv не найдено!
    echo Выполните предварительную установку: powershell.exe -File scripts\install.ps1
    pause
)
