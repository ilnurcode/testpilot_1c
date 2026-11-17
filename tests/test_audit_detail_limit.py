"""Предел детализации замечаний: полный счётчик, ограниченный срез и явная пометка."""
import csv
import io

from fastapi.testclient import TestClient

from testpilot.scanner import MAX_ISSUES_KEPT

CONCAT_LINE = 'Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары ГДЕ Код = " + Код;\n'


def _web():
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    return web


def _scan(clients, issues: int) -> dict:
    web = _web()
    response = web.post('/api/v1/audit/scan', json={'code': CONCAT_LINE * issues})
    assert response.status_code == 200, response.text
    return response.json()


def test_scan_caps_details_but_counts_every_issue(clients):
    result = _scan(clients, 6000)

    assert result['total_issues'] == 6000
    assert result['issues_kept'] == MAX_ISSUES_KEPT == 5000
    assert result['issues_truncated'] is True
    assert len(result['issues']) == 5000
    assert [issue['rule_id'] for issue in result['issues'][:2]] == ['SEC002', 'SEC002']


def test_severity_distribution_counts_all_issues_not_kept_slice(clients):
    result = _scan(clients, 6000)

    assert sum(result['issues_by_severity'].values()) == 6000
    assert result['issues_by_severity'] == {'HIGH': 6000}
    assert sum(result['issues_by_category'].values()) == 6000
    assert result['passed'] is False


def test_scan_with_few_issues_is_not_truncated(clients):
    result = _scan(clients, 10)

    assert result['total_issues'] == 10
    assert result['issues_kept'] == 10
    assert result['issues_truncated'] is False
    assert len(result['issues']) == 10


def test_get_scan_returns_same_truncation_fields(clients):
    posted = _scan(clients, 6000)
    response = _web().get(f"/api/v1/audit/scans/{posted['scan_id']}")
    assert response.status_code == 200, response.text
    fetched = response.json()

    for key in ('total_issues', 'issues_kept', 'issues_truncated', 'issues_by_severity', 'issues_by_category', 'passed'):
        assert fetched[key] == posted[key], key
    assert len(fetched['issues']) == 5000


def test_markdown_report_states_truncation(clients):
    posted = _scan(clients, 6000)
    response = _web().get(f"/api/v1/audit/scans/{posted['scan_id']}/report")
    assert response.status_code == 200, response.text

    assert 'показаны первые 5000 из 6000 замечаний (детализация ограничена)' in response.text
    assert '- Всего замечаний: 6000' in response.text
    assert response.headers['X-Audit-Issues-Truncated'] == 'true'


def test_csv_report_has_header_plus_kept_rows(clients):
    posted = _scan(clients, 6000)
    response = _web().get(f"/api/v1/audit/scans/{posted['scan_id']}/report?format=csv")
    assert response.status_code == 200, response.text
    assert response.headers['X-Audit-Issues-Kept'] == '5000'
    assert response.headers['X-Audit-Issues-Total'] == '6000'

    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert len(rows) == 5001
    assert rows[0] == ['Правило', 'Уровень', 'Файл:строка', 'Сообщение', 'Фрагмент кода']
    assert rows[1][0] == 'SEC002'


def test_report_without_truncation_has_no_note(clients):
    posted = _scan(clients, 10)
    md = _web().get(f"/api/v1/audit/scans/{posted['scan_id']}/report").text
    assert 'детализация ограничена' not in md
    csv_response = _web().get(f"/api/v1/audit/scans/{posted['scan_id']}/report?format=csv")
    assert csv_response.headers['X-Audit-Issues-Truncated'] == 'false'
    rows = list(csv.reader(io.StringIO(csv_response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert len(rows) == 11
