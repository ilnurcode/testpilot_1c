"""Страж полноты docs/api.md: каждый маршрут OpenAPI должен быть описан.

Ограничение: проверка односторонняя — новый маршрут без документации валит тест.
Обратное (описан маршрут, которого нет в коде) не проверяется: разбор свободного
текста api.md не даёт надёжного множества описанных путей.
"""
from __future__ import annotations

import re

METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')

# Намеренно не документируются: служебные маршруты фреймворка и статика.
EXCEPTIONS = {
    ('GET', '/openapi.json'): 'машинная схема OpenAPI, генерируется FastAPI',
    ('GET', '/docs'): 'встроенная Swagger UI',
    ('GET', '/redoc'): 'встроенная ReDoc UI',
    ('GET', '/health'): 'описан текстом в шапке docs/api.md (без таблицы)',
    ('GET', '/static/{}'): 'статические файлы UI',
    ('GET', '/favicon.ico'): 'иконка браузера',
}

DOC = 'docs/api.md'


def _norm(path: str) -> str:
    path = path.split('?', 1)[0].strip().rstrip('/')
    path = re.sub(r'\{[^}]*\}', '{}', path)
    return path.lower() or '/'


def _documented(doc_text: str) -> set[tuple[str, str]]:
    """(метод, нормализованный путь) из таблиц и inline-упоминаний api.md."""
    pairs: set[tuple[str, str]] = set()
    prefix = '/api/v1'
    for line in doc_text.splitlines():
        header = re.search(r'`(/api/v1[^`]*)`', line)
        if header and 'Путь' in line:
            prefix = header.group(1).replace('…', '').rstrip('/') or '/api/v1'

        if line.strip().startswith('|'):
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            if len(cells) >= 2 and not set(cells[0]) <= {'-', ' '} and 'Метод' not in cells[0]:
                methods = [m.upper() for m in re.findall(r'\b(GET|POST|PUT|PATCH|DELETE)\b', cells[0], re.I)]
                if methods:
                    for variant in re.split(r'\s*,\s*|\s+/\s+', cells[1]):
                        variant = variant.strip().strip('`').split('?', 1)[0]
                        if not variant or not re.match(r'^[A-Za-z0-9_{}/.\-]+$', variant):
                            continue
                        full = variant if variant.startswith('/') else f'{prefix}/{variant.lstrip("/")}'
                        for method in methods:
                            pairs.add((method, _norm(full)))

        for method, path in re.findall(r'\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}/.\-]+)', line):
            pairs.add((method.upper(), _norm(path)))
    return pairs


def _openapi_routes() -> list[tuple[str, str, str]]:
    from testpilot.impact.api import app as impact_app
    from testpilot.intelligence.api import app as intelligence_app
    from testpilot.web.api import app as web_app

    routes: list[tuple[str, str, str]] = []
    for service, app in (('web', web_app), ('impact', impact_app), ('intelligence', intelligence_app)):
        for path, operations in app.openapi()['paths'].items():
            for method in operations:
                if method.upper() in METHODS:
                    routes.append((service, method.upper(), path))
    return routes


def test_every_openapi_route_is_documented_in_api_md():
    from pathlib import Path

    documented = _documented((Path(__file__).parent.parent / DOC).read_text(encoding='utf-8'))
    routes = _openapi_routes()
    assert len(routes) >= 60, f'маршрутов подозрительно мало: {len(routes)}'

    undocumented = []
    for service, method, path in routes:
        key = (method, _norm(path))
        if key in EXCEPTIONS or (method, path) in EXCEPTIONS:
            continue
        if key in documented:
            continue
        stripped = _norm(path[len('/api/v1'):]) if path.startswith('/api/v1/') else None
        if stripped and (method, stripped) in documented:
            continue
        undocumented.append(f'{service}: {method} {path}')

    assert not undocumented, "нет в docs/api.md:\n" + "\n".join(sorted(undocumented))
