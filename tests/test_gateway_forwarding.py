"""Шлюз пробрасывает метод, путь, query и тело в апстрим; сеть подменяется заглушкой."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

GATEWAY = '/gateway/{service}/{path:path}'
BASIC = ('expert', 'test-password-only')


class Upstream:
    """Заглушка httpx.AsyncClient: запоминает вызов и отдаёт заданный ответ или ошибку."""

    def __init__(self, response: httpx.Response | None = None, error: Exception | None = None) -> None:
        self.response = response or httpx.Response(200, json={'ok': True}, headers={'content-type': 'application/json'})
        self.error = error
        self.calls: list[dict] = []

    async def __aenter__(self) -> 'Upstream':
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def request(self, method, url, params=None, content=None, headers=None):  # noqa: ANN001
        self.calls.append({'method': method, 'url': url, 'params': dict(params or {}),
                           'content': content, 'headers': dict(headers or {})})
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv('TP_EXPERT_PASSWORD', BASIC[1])
    monkeypatch.setenv('TP_ANALYST_PASSWORD', BASIC[1])
    monkeypatch.setenv('TP_RUNNER_PASSWORD', BASIC[1])
    from testpilot.web import api

    client = TestClient(api.app)
    client.auth = BASIC
    return api, client


def _install(monkeypatch, api, upstream: Upstream) -> Upstream:
    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **kwargs: upstream)
    return upstream


def test_post_forwards_method_path_and_body(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())
    body = b'{"name":"probe","value":42}'

    response = client.post('/gateway/impact/api/v1/projects?limit=5', content=body,
                           headers={'Content-Type': 'application/json'})

    assert response.status_code == 200
    assert len(upstream.calls) == 1
    call = upstream.calls[0]
    assert call['method'] == 'POST'
    assert call['url'] == 'http://127.0.0.1:8001/api/v1/projects'
    assert call['params'] == {'limit': '5'}
    assert call['content'] == body


def test_put_forwards_method_and_body(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())
    body = b'{"field":"value"}'

    response = client.put('/gateway/intelligence/api/v1/tests/abc', content=body,
                          headers={'Content-Type': 'application/json'})

    assert response.status_code == 200
    call = upstream.calls[0]
    assert call['method'] == 'PUT'
    assert call['url'] == 'http://127.0.0.1:8002/api/v1/tests/abc'
    assert call['content'] == body


def test_get_has_empty_body_and_get_method(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())

    response = client.get('/gateway/impact/api/v1/versions')

    assert response.status_code == 200
    call = upstream.calls[0]
    assert call['method'] == 'GET'
    assert call['content'] in (b'', None)


def test_delete_forwards_delete_method(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())

    response = client.delete('/gateway/impact/api/v1/projects/x?confirm=x')

    assert response.status_code == 200
    call = upstream.calls[0]
    assert call['method'] == 'DELETE'
    assert call['params'] == {'confirm': 'x'}


def test_authorization_is_forwarded_as_is(web, monkeypatch):
    """Фактическое поведение: шлюз передаёт Authorization апстриму (impact/intelligence требуют те же креды)."""
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())

    client.get('/gateway/impact/api/v1/versions')

    authorization = upstream.calls[0]['headers'].get('Authorization')
    assert isinstance(authorization, str) and authorization.startswith('Basic ')


def test_unknown_service_is_rejected_without_upstream_call(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())

    response = client.get('/gateway/other/api/v1/versions')

    assert response.status_code == 404
    assert response.json()['error']['message'] == 'Неизвестный API'
    assert upstream.calls == []


def test_path_outside_api_v1_is_rejected(web, monkeypatch):
    api, client = web
    upstream = _install(monkeypatch, api, Upstream())

    response = client.get('/gateway/impact/health')

    assert response.status_code == 404
    assert upstream.calls == []


def test_upstream_http_error_becomes_503(web, monkeypatch):
    api, client = web
    _install(monkeypatch, api, Upstream(error=httpx.HTTPError('boom')))

    response = client.get('/gateway/impact/api/v1/versions')

    assert response.status_code == 503
    assert response.json()['error']['message'] == 'Сервис временно недоступен'


def test_upstream_status_and_json_body_are_passed_through(web, monkeypatch):
    api, client = web
    upstream = Upstream(response=httpx.Response(404, json={'error': {'code': 404, 'message': 'нет'}},
                                                headers={'content-type': 'application/json', 'set-cookie': 'x=1'}))
    _install(monkeypatch, api, upstream)

    response = client.get('/gateway/impact/api/v1/missing')

    assert response.status_code == 404
    assert response.json() == {'error': {'code': 404, 'message': 'нет'}}
    assert response.headers['content-type'].startswith('application/json')
    assert 'set-cookie' not in {name.lower() for name in response.headers}
