"""Постраничная выдача замечаний аудита загрузки."""
import io
from zipfile import ZipFile

from fastapi.testclient import TestClient

from testpilot.scanner import MAX_ISSUES_KEPT

CONCAT_LINE = 'Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары ГДЕ Код = " + Код;\n'


def _web() -> TestClient:
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    return web


def _upload(clients, lines: int, *, audit: bool = True):
    impact, _ = clients
    project = impact.post('/api/v1/projects', json={'name': f'Проект пагинации {lines}', 'description': ''}).json()
    body = io.BytesIO()
    with ZipFile(body, 'w') as archive:
        archive.writestr('Module.bsl', CONCAT_LINE * lines)
    response = impact.post(
        f"/api/v1/projects/{project['id']}/uploads",
        params={'filename': 'Sources.zip'},
        content=body.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    upload = response.json()
    if audit:
        scanned = _web().post('/api/v1/audit/scan-upload',
                              json={'project_id': project['id'], 'upload_id': upload['id']})
        assert scanned.status_code == 200, scanned.text
    return project, upload


def _page(upload_id: str, offset: int = 0, limit: int = 100) -> dict:
    response = _web().get(f'/api/v1/audit/uploads/{upload_id}/issues',
                          params={'offset': offset, 'limit': limit})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_issues_endpoint_rejects_bad_bounds(clients):
    _, upload = _upload(clients, 10)
    web = _web()

    assert web.get(f"/api/v1/audit/uploads/{upload['id']}/issues", params={'limit': 0}).status_code == 400
    assert web.get(f"/api/v1/audit/uploads/{upload['id']}/issues", params={'limit': 1001}).status_code == 400
    assert web.get(f"/api/v1/audit/uploads/{upload['id']}/issues", params={'offset': -1}).status_code == 400


def test_upload_issues_endpoint_returns_404_without_audit(clients):
    _, upload = _upload(clients, 10, audit=False)
    web = _web()

    assert web.get(f"/api/v1/audit/uploads/{upload['id']}/issues").status_code == 404
    assert web.get(f"/api/v1/audit/uploads/{upload['id']}").status_code == 404


def test_upload_issues_pages_cover_first_200_without_overlap(clients):
    _, upload = _upload(clients, 6000)
    first = _page(upload['id'], offset=0, limit=100)
    second = _page(upload['id'], offset=100, limit=100)

    lines_first = [issue['line'] for issue in first['items']]
    lines_second = [issue['line'] for issue in second['items']]
    assert len(lines_first) == 100 and len(lines_second) == 100
    assert set(lines_first).isdisjoint(set(lines_second))
    assert lines_first[0] == 1 and lines_first[-1] == 100
    assert lines_second[0] == 101 and lines_second[-1] == 200


def test_upload_issues_total_is_full_while_details_are_capped(clients):
    _, upload = _upload(clients, 6000)
    page = _page(upload['id'], offset=0, limit=10)

    assert page['total'] == 6000
    assert page['issues_kept'] == MAX_ISSUES_KEPT == 5000
    assert page['issues_truncated'] is True
    assert len(page['items']) == 10
    boundary = _page(upload['id'], offset=5000, limit=100)
    assert boundary['items'] == [] and boundary['total'] == 6000
    last = _page(upload['id'], offset=4999, limit=100)
    assert len(last['items']) == 1 and last['items'][0]['line'] == 5000


def test_upload_card_omits_large_issues_and_points_to_pages(clients):
    _, upload = _upload(clients, 6000)
    card = _web().get(f"/api/v1/audit/uploads/{upload['id']}").json()

    assert card.get('issues') is None
    assert card['issues_url'] == f"/api/v1/audit/uploads/{upload['id']}/issues"
    assert card['issues_kept'] == 5000
    assert card['total_issues'] == 6000
    assert card['issues_truncated'] is True
    assert card['issues_by_severity'] == {'HIGH': 6000}


def test_upload_card_small_audit_keeps_issues_and_shape(clients):
    _, upload = _upload(clients, 12)
    card = _web().get(f"/api/v1/audit/uploads/{upload['id']}").json()

    assert len(card['issues']) == 12
    assert card['total_issues'] == 12
    assert card['issues_kept'] == 12
    assert card['issues_truncated'] is False
    assert card['issues_url'] == f"/api/v1/audit/uploads/{upload['id']}/issues"
    assert card['issues_by_severity'] == {'HIGH': 12}
    assert card['status'] == 'scanned'
    page = _page(upload['id'], offset=0, limit=100)
    assert len(page['items']) == 12 and page['total'] == 12
