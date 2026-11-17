"""Настраиваемый предел детализации аудита: разбор, валидация, /health, применение."""
import pytest
from fastapi.testclient import TestClient

from testpilot import audit_limit
from testpilot.audit_limit import (
    DEFAULT_MAX_ISSUES_KEPT,
    ENV_VAR,
    MAX_MAX_ISSUES_KEPT,
    MIN_MAX_ISSUES_KEPT,
    resolve_max_issues_kept,
)

CONCAT_LINE = 'Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары ГДЕ Код = " + Код;\n'


def _web() -> TestClient:
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    return web


def _scan(clients, issues: int) -> dict:
    response = _web().post('/api/v1/audit/scan', json={'code': CONCAT_LINE * issues})
    assert response.status_code == 200, response.text
    return response.json()


def test_audit_limit_defaults_without_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)

    assert DEFAULT_MAX_ISSUES_KEPT == 5000
    assert resolve_max_issues_kept() == 5000
    assert resolve_max_issues_kept('') == 5000
    assert resolve_max_issues_kept('  ') == 5000
    assert audit_limit.MAX_ISSUES_KEPT == 5000


def test_audit_limit_accepts_bounds_and_env(monkeypatch):
    monkeypatch.setenv(ENV_VAR, '100')

    assert resolve_max_issues_kept() == MIN_MAX_ISSUES_KEPT == 100
    assert resolve_max_issues_kept(str(MAX_MAX_ISSUES_KEPT)) == 20000
    assert resolve_max_issues_kept('250') == 250


@pytest.mark.parametrize('raw', ['99', '20001', '0', '-5', 'abc', '1e5', '5 000'])
def test_audit_limit_rejects_invalid_values_at_startup(raw):
    with pytest.raises(ValueError) as error:
        resolve_max_issues_kept(raw)

    message = str(error.value)
    assert ENV_VAR in message
    assert '100' in message and '20000' in message


def test_health_reports_effective_limit(clients, monkeypatch):
    web = _web()

    assert web.get('/health').json()['audit_max_issues'] == audit_limit.MAX_ISSUES_KEPT

    monkeypatch.setattr(audit_limit, 'MAX_ISSUES_KEPT', 100)
    assert web.get('/health').json()['audit_max_issues'] == 100


def test_limit_100_caps_scan_details_but_keeps_total(clients, monkeypatch):
    monkeypatch.setattr(audit_limit, 'MAX_ISSUES_KEPT', 100)
    result = _scan(clients, 600)

    assert result['total_issues'] == 600
    assert result['issues_kept'] == 100
    assert result['issues_truncated'] is True
    assert result.get('issues') in (None, [])
    assert sum(result['issues_by_severity'].values()) == 600

    page = _web().get(f"/api/v1/audit/scans/{result['scan_id']}/issues",
                      params={'offset': 0, 'limit': 100}).json()
    assert len(page['items']) == 100
    assert page['total'] == 600 and page['issues_kept'] == 100
    beyond = _web().get(f"/api/v1/audit/scans/{result['scan_id']}/issues",
                        params={'offset': 100, 'limit': 100}).json()
    assert beyond['items'] == [] and beyond['total'] == 600


def test_limit_100_caps_upload_audit_the_same_way(clients, monkeypatch):
    import io
    from zipfile import ZipFile

    monkeypatch.setattr(audit_limit, 'MAX_ISSUES_KEPT', 100)
    impact, _ = clients
    project = impact.post('/api/v1/projects', json={'name': 'Предел 100', 'description': ''}).json()
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as zip_file:
        zip_file.writestr('Module.bsl', CONCAT_LINE * 600)
    upload = impact.post(f"/api/v1/projects/{project['id']}/uploads",
                         params={'filename': 'Sources.zip'},
                         content=archive.getvalue(),
                         headers={'Content-Type': 'application/octet-stream'}).json()
    web = _web()
    scanned = web.post('/api/v1/audit/scan-upload',
                       json={'project_id': project['id'], 'upload_id': upload['id']}).json()

    assert scanned['total_issues'] == 600
    assert scanned['issues_kept'] == 100
    assert scanned.get('issues') in (None, [])

    card = web.get(f"/api/v1/audit/uploads/{upload['id']}").json()
    assert card['total_issues'] == 600 and card['issues_kept'] == 100
    assert 'issues' not in card
    page = web.get(f"/api/v1/audit/uploads/{upload['id']}/issues",
                   params={'offset': 0, 'limit': 100}).json()
    assert len(page['items']) == 100 and page['total'] == 600
