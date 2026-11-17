"""Адресное удаление записи журнала аудита кода."""
from fastapi.testclient import TestClient

CONCAT_LINE = 'Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары ГДЕ Код = " + Код;\n'


def _web() -> TestClient:
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    return web


def _scan(n: int = 3) -> dict:
    response = _web().post('/api/v1/audit/scan', json={'code': CONCAT_LINE * n})
    assert response.status_code == 200, response.text
    return response.json()


def test_delete_without_confirm_is_rejected_and_keeps_record(clients):
    scan = _scan()
    web = _web()

    response = web.delete(f"/api/v1/audit/scans/{scan['scan_id']}")

    assert response.status_code == 409, response.text
    assert f"confirm={scan['scan_id']}" in response.text
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}").status_code == 200


def test_delete_with_wrong_confirm_is_rejected(clients):
    scan = _scan()
    web = _web()

    response = web.delete(f"/api/v1/audit/scans/{scan['scan_id']}", params={'confirm': 'not-the-id'})

    assert response.status_code == 409
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}").status_code == 200


def test_delete_with_confirm_removes_only_that_record(clients):
    scan = _scan()
    web = _web()

    response = web.delete(f"/api/v1/audit/scans/{scan['scan_id']}", params={'confirm': scan['scan_id']})

    assert response.status_code == 200, response.text
    assert response.json() == {'scan_id': scan['scan_id'], 'deleted': True}
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}").status_code == 404
    listed = {row['scan_id'] for row in web.get('/api/v1/audit/scans', params={'limit': 100}).json()}
    assert scan['scan_id'] not in listed


def test_delete_unknown_scan_returns_404(clients):
    missing = '3c7a1f22-0000-4000-8000-000000000000'
    response = _web().delete(f'/api/v1/audit/scans/{missing}', params={'confirm': missing})

    assert response.status_code == 404


def test_delete_does_not_touch_another_scan(clients):
    first, second = _scan(3), _scan(4)
    web = _web()

    assert web.delete(f"/api/v1/audit/scans/{first['scan_id']}", params={'confirm': first['scan_id']}).status_code == 200

    survivor = web.get(f"/api/v1/audit/scans/{second['scan_id']}")
    assert survivor.status_code == 200
    assert survivor.json()['total_issues'] == second['total_issues']
    assert survivor.json()['scan_id'] == second['scan_id']


def test_delete_requires_expert_role(clients):
    scan = _scan()
    web = _web()

    response = web.delete(f"/api/v1/audit/scans/{scan['scan_id']}",
                          params={'confirm': scan['scan_id']},
                          auth=('analyst', 'test-password-only'))

    assert response.status_code == 403
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}").status_code == 200
