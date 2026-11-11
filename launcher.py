"""Автономный лаунчер TestPilot 1C для Windows.
Запускает сервисы, проверяет готовность и открывает браузер.
"""
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent

def is_ready(url="http://127.0.0.1:8000/health", timeout=1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False

def start_services():
    print("Запуск сервисов TestPilot 1C...")
    start_script = ROOT / "scripts" / "start.ps1"
    
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(start_script)
    ]
    
    # Запуск без отображения назойливого окна или в фоне
    creation_flags = 0
    if os.name == 'nt':
        creation_flags = subprocess.CREATE_NO_WINDOW

    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, creationflags=creation_flags)
    if proc.returncode != 0:
        print(f"Ошибка запуска сервисов:\n{proc.stderr}\n{proc.stdout}", file=sys.stderr)
        return False
    return True

def main():
    print("=" * 50)
    print("   TestPilot 1C — Система регрессионного тестирования")
    print("=" * 50)

    if is_ready():
        print("Сервисы уже запущены!")
    else:
        success = start_services()
        if not success:
            input("\nНажмите Enter для выхода...")
            sys.exit(1)

        # Ожидание готовности
        print("Ожидание готовности веб-интерфейса...")
        deadline = time.time() + 30
        while time.time() < deadline:
            if is_ready():
                break
            time.sleep(1)

    url = "http://localhost:8000"
    print(f"\nГотово! Открытие веб-интерфейса: {url}")
    webbrowser.open(url)
    print("\nСервис работает в фоновом режиме.")
    print("Для остановки запустите: scripts\\stop.ps1 или 'Остановить TestPilot.cmd'")
    time.sleep(3)

if __name__ == '__main__':
    main()
