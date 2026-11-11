@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================================
echo   Запуск TestPilot 1C в Docker Compose...
echo ======================================================================

docker compose up -d
if errorlevel 1 (
    echo [ОШИБКА] Не удалось запустить Docker контейнеры.
    echo Убедитесь, что Docker Desktop запущен.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo   TestPilot 1C успешно запущен в Docker!
echo   Интерфейс: http://localhost:8000
echo   Логин:     expert
echo   Пароль:    TestPilotLocal123!
echo ======================================================================
echo.
timeout /t 3 >nul
start http://localhost:8000
