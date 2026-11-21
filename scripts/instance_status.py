"""Готовность инстанса: идентичность приложения/БД и health всех трёх сервисов.

Схему не меняет, секреты не печатает. Использование:
    python scripts/instance_status.py [--require-ready] [--write-receipt PATH] [--timeout SECONDS]

Код возврата: 0 — готов, 2 — не готов, 4 — нет DATABASE_URL/ошибка чтения состояния.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from testpilot.schema_state import STATE_OK, describe, engine_for, schema_error_message

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {
    'web': ('WEB_URL', 'http://127.0.0.1:8000'),
    'impact': ('IMPACT_URL', 'http://127.0.0.1:8001'),
    'intelligence': ('INTELLIGENCE_URL', 'http://127.0.0.1:8002'),
}


def app_version() -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        return 'unknown'
    try:
        with open(ROOT / 'pyproject.toml', 'rb') as handle:
            return str(tomllib.load(handle).get('project', {}).get('version', 'unknown'))
    except (OSError, ValueError):
        return 'unknown'


def probe(url: str, timeout: float = 2.0) -> dict:
    """Один запрос /health без секретов в отчёте."""
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/health', timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8', errors='replace') or '{}')
            instance = body.get('instance') if isinstance(body, dict) else None
            return {
                'url': url,
                'http_status': response.status,
                'status': body.get('status') if isinstance(body, dict) else None,
                'instance': {
                    'database': (instance or {}).get('database'),
                    'marker_ok': (instance or {}).get('marker_ok'),
                },
            }
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            payload = json.loads(exc.read().decode('utf-8', errors='replace') or '{}')
            detail = str(payload.get('error', {}).get('message', ''))[:500]
        except (ValueError, AttributeError):
            detail = ''
        return {'url': url, 'http_status': exc.code, 'status': 'error', 'detail': detail}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {'url': url, 'http_status': None, 'status': 'unreachable', 'detail': type(exc).__name__}


def schema_report(dsn: str) -> dict:
    engine = engine_for(dsn)
    try:
        with engine.connect() as connection:
            return describe(connection)
    except Exception as exc:  # noqa: BLE001 - только тип ошибки
        return {'state': 'unreachable', 'current': None, 'heads': [], 'error': type(exc).__name__}
    finally:
        engine.dispose()


def collect(dsn: str) -> dict:
    schema = schema_report(dsn)
    services = {}
    for name, (env_key, default) in SERVICES.items():
        services[name] = probe(os.environ.get(env_key, default))
    ready = (
        schema['state'] == STATE_OK
        and all(item['http_status'] == 200 and item['status'] == 'ok' for item in services.values())
    )
    return {
        'ready': ready,
        'app_version': app_version(),
        'schema': schema,
        'services': services,
        'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


def write_receipt(path: Path, report: dict) -> None:
    """Атомарная публикация: временный файл рядом, затем replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Готовность инстанса TestPilot: схема и health всех сервисов.')
    parser.add_argument('--require-ready', action='store_true', help='код 2, если что-то не готово')
    parser.add_argument('--write-receipt', default=None, help='атомарно записать JSON-квитанцию по пути')
    parser.add_argument('--timeout', type=float, default=0.0, help='ждать готовности не дольше N секунд (по умолчанию один замер)')
    parser.add_argument('--json', action='store_true', help='печать отчёта JSON')
    args = parser.parse_args(argv)

    dsn = (os.environ.get('DATABASE_URL') or '').strip()
    if not dsn:
        print('DATABASE_URL не задан: загрузите .env или запустите через scripts/start.ps1.', file=sys.stderr)
        return 4

    deadline = time.monotonic() + max(args.timeout, 0.0)
    report = collect(dsn)
    while not report['ready'] and time.monotonic() < deadline:
        time.sleep(0.5)
        report = collect(dsn)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        schema = report['schema']
        print(f"Версия приложения: {report['app_version']}")
        print(f"Схема БД: {schema['state']} (БД={schema.get('current') or 'нет'}, код={', '.join(schema.get('heads') or []) or 'нет'})")
        for name, item in report['services'].items():
            print(f"{name}: http={item['http_status']} status={item['status']}")
        print('Готовность: ' + ('ok' if report['ready'] else 'не готов'))
    if not report['ready']:
        if report['schema']['state'] != STATE_OK:
            print(schema_error_message(report['schema']), file=sys.stderr)
        else:
            failed = [name for name, item in report['services'].items()
                      if item['http_status'] != 200 or item['status'] != 'ok']
            print('Не готовы сервисы: ' + ', '.join(failed), file=sys.stderr)

    if args.write_receipt:
        try:
            write_receipt(Path(args.write_receipt), report)
        except OSError as exc:
            print(f'Квитанция не записана: {type(exc).__name__}.', file=sys.stderr)
            return 2

    return 0 if report['ready'] or not args.require_ready else 2


if __name__ == '__main__':
    raise SystemExit(main())
