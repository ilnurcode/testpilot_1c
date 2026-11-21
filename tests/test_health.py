"""Честные поля /health: env-профиль 1С отдельно от подключений проектов."""
from __future__ import annotations

# `instance` — идентичность БД из A1 (database + marker_ok), без токена и DSN.
HEALTH_FIELDS = {'status', 'executor_1c_env', 'projects_with_connection', 'audit_max_issues', 'instance'}


def _connect_project(impact) -> dict:
    project = impact.post('/api/v1/projects', json={'name': 'Проект с подключением', 'description': ''}).json()
    response = impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={'odata_url': 'https://1c.example.test/odata/standard.odata'},
    )
    assert response.status_code == 200, response.text
    return project


def test_health_reports_env_profile_and_project_connections(clients, monkeypatch):
    impact, _ = clients
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '1c.example.test')
    monkeypatch.delenv('TP_1C_ODATA_URL', raising=False)
    monkeypatch.delenv('TP_1C_WEB_URL', raising=False)
    _connect_project(impact)

    response = impact.get('/health')

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == HEALTH_FIELDS
    assert body['status'] == 'ok'
    assert body['executor_1c_env'] == 'not_configured'
    assert body['projects_with_connection'] >= 1
    assert 'executor_1c' not in body
    assert set(body['instance']) == {'database', 'marker_ok'}
    assert isinstance(body['instance']['database'], str)
    assert body['instance']['marker_ok'] is False


def test_health_reports_configured_env_profile(clients, monkeypatch):
    impact, _ = clients
    monkeypatch.setenv('TP_1C_ODATA_URL', 'https://1c.example.test/odata/standard.odata')

    body = impact.get('/health').json()

    assert set(body) == HEALTH_FIELDS
    assert body['executor_1c_env'] == 'configured'


def test_health_counts_only_projects_with_connection_fields(clients, monkeypatch):
    impact, _ = clients
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '1c.example.test')
    monkeypatch.delenv('TP_1C_ODATA_URL', raising=False)
    monkeypatch.delenv('TP_1C_WEB_URL', raising=False)
    before = impact.get('/health').json()['projects_with_connection']
    impact.post('/api/v1/projects', json={'name': 'Проект без подключения', 'description': ''})

    after = impact.get('/health').json()['projects_with_connection']
    assert after == before

    _connect_project(impact)
    assert impact.get('/health').json()['projects_with_connection'] == before + 1
