"""Постраничная выдача замечаний сохранённого аудита."""
from fastapi.testclient import TestClient

from testpilot.scanner import MAX_ISSUES_KEPT

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


def _page(scan_id: str, offset: int = 0, limit: int = 100) -> dict:
    response = _web().get(f'/api/v1/audit/scans/{scan_id}/issues',
                          params={'offset': offset, 'limit': limit})
    assert response.status_code == 200, response.text
    return response.json()


def test_issues_endpoint_rejects_bad_bounds(clients):
    scan = _scan(clients, 10)
    web = _web()

    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}/issues", params={'limit': 0}).status_code == 400
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}/issues", params={'limit': 1001}).status_code == 400
    assert web.get(f"/api/v1/audit/scans/{scan['scan_id']}/issues", params={'offset': -1}).status_code == 400


def test_issues_endpoint_returns_404_for_unknown_scan(clients):
    web = _web()
    assert web.get('/api/v1/audit/scans/6b1f0f3e-0000-4000-8000-000000000000/issues').status_code == 404


def test_issues_pages_do_not_overlap_and_cover_first_200(clients):
    scan = _scan(clients, 6000)
    first = _page(scan['scan_id'], offset=0, limit=100)
    second = _page(scan['scan_id'], offset=100, limit=100)

    assert len(first['items']) == 100 and len(second['items']) == 100
    lines_first = [(issue['file_path'], issue['line']) for issue in first['items']]
    lines_second = [(issue['file_path'], issue['line']) for issue in second['items']]
    assert set(lines_first).isdisjoint(set(lines_second))
    assert lines_first[0] == ('web_input.bsl', 1)
    assert lines_first[-1] == ('web_input.bsl', 100)
    assert lines_second[0] == ('web_input.bsl', 101)
    assert lines_second[-1] == ('web_input.bsl', 200)


def test_issues_total_is_full_count_while_details_are_capped(clients):
    scan = _scan(clients, 6000)
    page = _page(scan['scan_id'], offset=0, limit=10)

    assert page['total'] == 6000
    assert page['issues_kept'] == MAX_ISSUES_KEPT == 5000
    assert page['issues_truncated'] is True
    assert page['offset'] == 0 and page['limit'] == 10
    assert len(page['items']) == 10


def test_issues_beyond_kept_slice_are_empty_but_total_stays(clients):
    scan = _scan(clients, 6000)
    page = _page(scan['scan_id'], offset=5000, limit=100)

    assert page['items'] == []
    assert page['total'] == 6000
    assert page['issues_kept'] == 5000


def test_issues_order_is_stable_between_requests(clients):
    scan = _scan(clients, 300)
    first = _page(scan['scan_id'], offset=0, limit=50)
    again = _page(scan['scan_id'], offset=0, limit=50)
    later = _page(scan['scan_id'], offset=250, limit=50)

    assert first['items'] == again['items']
    keys = [(issue['file_path'], issue['line'], issue['col']) for issue in first['items']]
    assert keys == sorted(keys)
    tail = [(issue['file_path'], issue['line'], issue['col']) for issue in later['items']]
    assert tail[0] == ('web_input.bsl', 251, 1)
    assert tail == sorted(tail)


def test_small_scan_detail_response_keeps_issues_and_url(clients):
    scan = _scan(clients, 10)
    web = _web()
    fetched = web.get(f"/api/v1/audit/scans/{scan['scan_id']}").json()

    assert len(fetched['issues']) == 10
    assert fetched['issues_url'] == f"/api/v1/audit/scans/{scan['scan_id']}/issues"
    page = _page(scan['scan_id'], offset=0, limit=100)
    assert len(page['items']) == 10
    assert page['total'] == 10 and page['issues_truncated'] is False


def test_issues_offset_at_kept_boundary_returns_empty_page(clients):
    scan = _scan(clients, 6000)
    page = _page(scan['scan_id'], offset=5000, limit=100)

    assert page['items'] == []
    assert page['total'] == 6000
    assert page['issues_kept'] == 5000


def test_issues_last_saved_item_is_reachable(clients):
    scan = _scan(clients, 6000)
    page = _page(scan['scan_id'], offset=4999, limit=100)

    assert len(page['items']) == 1
    assert page['items'][0]['line'] == 5000
    assert page['total'] == 6000
