"""Автономный лаунчер TestPilot 1C для Windows.

Запускает сервисы через scripts/start.ps1, ждёт готовности всех трёх сервисов
(web, impact, intelligence) и открывает браузер только после неё.
Таймаут или ошибка запуска — ненулевой код возврата и браузер НЕ открывается.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 180.0
SERVICE_ORDER = ('web', 'impact', 'intelligence')


def service_urls() -> dict[str, str]:
    """Адреса локальной публикации: фиксированные порты Windows-native запуска."""
    return {
        'web': os.environ.get('WEB_URL') or 'http://127.0.0.1:8000',
        'impact': os.environ.get('IMPACT_URL') or 'http://127.0.0.1:8001',
        'intelligence': os.environ.get('INTELLIGENCE_URL') or 'http://127.0.0.1:8002',
    }


def probe(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """Готовность одного сервиса: HTTP 200 и status=ok в /health."""
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/health', timeout=timeout) as response:
            if response.status != 200:
                return False, f'HTTP {response.status}'
            try:
                body = json.loads(response.read().decode('utf-8', errors='replace') or '{}')
            except ValueError:
                return False, 'некорректный JSON /health'
            if not isinstance(body, dict) or body.get('status') != 'ok':
                return False, 'status != ok'
            return True, 'ok'
    except urllib.error.HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, type(exc).__name__


def readiness() -> dict[str, tuple[bool, str]]:
    return {name: probe(url) for name, url in service_urls().items()}


def all_ready(report: dict[str, tuple[bool, str]]) -> bool:
    return all(ready for ready, _ in report.values())


def wait_ready(timeout: float, interval: float = 1.0) -> dict[str, tuple[bool, str]]:
    """Ждёт готовности всех сервисов не дольше timeout секунд."""
    deadline = time.monotonic() + max(timeout, 0.0)
    report = readiness()
    while not all_ready(report) and time.monotonic() < deadline:
        time.sleep(interval)
        report = readiness()
    return report


def start_services() -> bool:
    print('Запуск сервисов TestPilot 1C...')
    start_script = ROOT / 'scripts' / 'start.ps1'
    if not start_script.is_file():
        print(f'Не найден скрипт запуска: {start_script}', file=sys.stderr)
        return False
    cmd = [
        'powershell.exe',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', str(start_script),
    ]
    creation_flags = 0
    if os.name == 'nt':
        creation_flags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, creationflags=creation_flags)
    if proc.returncode != 0:
        print('Ошибка запуска сервисов:', file=sys.stderr)
        print((proc.stdout or '').strip(), file=sys.stderr)
        print((proc.stderr or '').strip(), file=sys.stderr)
        return False
    return True


def _pause_if_interactive() -> None:
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        try:
            input('\nНажмите Enter для выхода...')
        except EOFError:
            pass


def _report_failure(report: dict[str, tuple[bool, str]]) -> None:
    print('TestPilot не готов: браузер не открыт.', file=sys.stderr)
    for name in SERVICE_ORDER:
        ready, status = report.get(name, (False, 'нет данных'))
        print(f'  {name}: {"ok" if ready else "не готов"} ({status})', file=sys.stderr)
    print('Проверьте логи .runtime/windows-*.error.log и выполните scripts\\start.ps1 вручную.', file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    timeout = float(os.environ.get('TP_START_TIMEOUT_SECONDS') or DEFAULT_TIMEOUT)
    print('=' * 50)
    print('   TestPilot 1C — Система регрессионного тестирования')
    print('=' * 50)

    report = readiness()
    if not all_ready(report):
        if not start_services():
            _report_failure(report)
            _pause_if_interactive()
            return 1
        print('Ожидание готовности web, impact и intelligence...')
        report = wait_ready(timeout)
        if not all_ready(report):
            _report_failure(report)
            _pause_if_interactive()
            return 1
    else:
        print('Сервисы уже запущены.')

    url = service_urls()['web'].rstrip('/')
    print(f'\nГотово! Открытие веб-интерфейса: {url}')
    webbrowser.open(url)
    print('\nСервис работает в фоновом режиме.')
    print("Для остановки запустите: scripts\\stop.ps1 или 'Остановить TestPilot.cmd'")
    time.sleep(3)
    return 0


if __name__ == '__main__':
    sys.exit(main())
