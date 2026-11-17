"""Шлюз `/gateway/{service}/{path}` принимает ровно GET/POST/PUT/DELETE, остальные методы — 405.

Тест не ходит в реальные сервисы: без Basic-креды зависимость отдаёт 401 ещё до httpx,
поэтому 401/404/503 означают «метод принят», а 405 — «метод не разрешён».
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

GATEWAY_URL = '/gateway/impact/api/v1/whatever'
ALLOWED = ('GET', 'POST', 'PUT', 'DELETE')
# PATCH/OPTIONS/TRACE не объявлены в маршруте; HEAD FastAPI тоже не добавляет автоматически.
REJECTED = ('PATCH', 'OPTIONS', 'TRACE', 'HEAD')
ACCEPTED_CODES = {401, 404, 503}


@pytest.fixture(scope='module')
def client():
    from testpilot.web.api import app

    return TestClient(app)


@pytest.mark.parametrize('method', ALLOWED)
def test_gateway_accepts_declared_methods(client, method):
    response = client.request(method, GATEWAY_URL)

    assert response.status_code in ACCEPTED_CODES, (
        f'{method} {GATEWAY_URL}: ожидался один из {sorted(ACCEPTED_CODES)}, получен {response.status_code}'
    )
    assert response.status_code != 405


@pytest.mark.parametrize('method', REJECTED)
def test_gateway_rejects_other_methods(client, method):
    response = client.request(method, GATEWAY_URL)

    assert response.status_code == 405, f'{method} {GATEWAY_URL}: ожидался 405, получен {response.status_code}'
    assert 'GET' in (response.headers.get('allow') or '')


def test_gateway_openapi_lists_exactly_four_methods():
    from testpilot.web.api import app

    operations = app.openapi()['paths']['/gateway/{service}/{path}']
    assert set(operations) == {'get', 'post', 'put', 'delete'}
    assert [spec['operationId'] for spec in operations.values()].count('gateway_get') == 1
