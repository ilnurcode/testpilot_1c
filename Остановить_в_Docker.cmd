@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================================
echo   Остановка TestPilot 1C в Docker Compose...
echo ======================================================================

docker compose down
if errorlevel 1 (
    echo [ОШИБКА] Сбой при остановке контейнеров.
    pause
    exit /b 1
)

echo.
echo [OK] Все контейнеры TestPilot 1C успешно остановлены.
pause
