"""Схема API и UI документации закрыты Basic Auth, пока явно не включён TP_OPENAPI_PUBLIC."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _new_app():
    from testpilot.common import app as make_app

    return make_app('TestPilot API')


def _credentials(monkeypatch) -> tuple[str, str]:
    monkeypatch.setenv('TP_EXPERT_PASSWORD', 'unit-password')
    return 'expert', 'unit-password'


def test_docs_and_schema_require_credentials_by_default(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.delenv('TP_OPENAPI_PUBLIC', raising=False)
    client = TestClient(_new_app())

    for path in ('/docs', '/redoc', '/openapi.json'):
        response = client.get(path)
        assert response.status_code == 401, f'{path}: {response.status_code}'
        assert response.headers.get('www-authenticate') == 'Basic'


def test_docs_and_schema_open_with_credentials(monkeypatch):
    username, password = _credentials(monkeypatch)
    monkeypatch.delenv('TP_OPENAPI_PUBLIC', raising=False)
    client = TestClient(_new_app())
    client.auth = (username, password)

    docs = client.get('/docs')
    assert docs.status_code == 200
    assert docs.headers['content-type'].startswith('text/html')
    assert 'swagger-ui' in docs.text

    redoc = client.get('/redoc')
    assert redoc.status_code == 200
    assert redoc.headers['content-type'].startswith('text/html')

    schema = client.get('/openapi.json')
    assert schema.status_code == 200
    assert schema.headers['content-type'].startswith('application/json')
    assert '/health' in schema.json()['paths']


def test_public_flag_opens_docs_without_credentials(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.setenv('TP_OPENAPI_PUBLIC', '1')
    client = TestClient(_new_app())

    assert client.get('/docs').status_code == 200
    assert client.get('/redoc').status_code == 200
    assert client.get('/openapi.json').status_code == 200


def test_rules_list_stays_public(monkeypatch):
    monkeypatch.delenv('TP_OPENAPI_PUBLIC', raising=False)
    from testpilot.web.api import app as web_app

    response = TestClient(web_app).get('/api/v1/audit/rules')

    assert response.status_code == 200
    assert {rule['rule_id'] for rule in response.json()} >= {'SEC013', 'SEC014'}
