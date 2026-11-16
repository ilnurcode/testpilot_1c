from __future__ import annotations

import csv
import hashlib
import io
from zipfile import ZipFile

from fastapi.responses import Response
from fastapi.testclient import TestClient

CODE = """Процедура Тест()
\tОтключитьБезопасныйРежим(Истина);
\tЗапрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары"
\t\t+ Переменная;
\tОбъект = Новый COMОбъект("Excel.Application");
КонецПроцедуры
"""

EXPECTED_COLUMNS = [
    'id',
    'source_name',
    'sha256',
    'total_files',
    'sources_checked',
    'total_issues',
    'issues_by_severity',
    'issues_by_category',
    'issues',
    'passed',
    'status',
    'message',
    'duration_ms',
    'created_at',
]


def _web() -> TestClient:
    from testpilot.web.api import app

    client = TestClient(app)
    client.auth = ('expert', 'test-password-only')
    return client


def _scan(web: TestClient, code: str = CODE) -> dict:
    response = web.post('/api/v1/audit/scan', json={'code': code})
    assert response.status_code == 200, response.text
    return response.json()


def test_audit_api_modules_import_and_expose_scanner_contract():
    from testpilot.scanner import ScannerEngine, ScanSummary, Severity
    from testpilot.web import api, audit_api

    assert api.app is not None
    assert audit_api.ScannerEngine is ScannerEngine
    assert audit_api.Severity is Severity
    assert audit_api.ScanSummary is ScanSummary
    assert callable(audit_api._code_audit_result)


def test_scan_saves_record_and_returns_scan_id(clients):
    from testpilot import db

    web = _web()
    created = _scan(web)
    assert created['scan_id']
    assert [issue['rule_id'] for issue in created['issues']] == ['SEC010', 'SEC011', 'SEC012']

    with db._factory() as s:
        row = s.get(db.CodeAuditRow, created['scan_id'])
        assert row is not None
        assert row.source_name == 'вставленный код'
        assert row.status == 'scanned'
        assert row.total_issues == 3
        assert row.passed is False
        assert row.sha256 == hashlib.sha256(CODE.strip().encode('utf-8')).hexdigest()


def test_scan_detail_is_identical_to_post_result(clients):
    web = _web()
    created = _scan(web)
    detail = web.get(f"/api/v1/audit/scans/{created['scan_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json() == created


def test_scan_list_rejects_bad_limits_and_unknown_id(clients):
    web = _web()
    created = _scan(web)

    assert web.get('/api/v1/audit/scans', params={'limit': 0}).status_code == 400
    assert web.get('/api/v1/audit/scans', params={'limit': 101}).status_code == 400

    listed = web.get('/api/v1/audit/scans', params={'limit': 1})
    assert listed.status_code == 200, listed.text
    assert [row['scan_id'] for row in listed.json()] == [created['scan_id']]

    missing = web.get('/api/v1/audit/scans/00000000-0000-0000-0000-000000000000')
    assert missing.status_code == 404


def test_scan_report_markdown_contains_sec011(clients):
    web = _web()
    created = _scan(web)
    response = web.get(f"/api/v1/audit/scans/{created['scan_id']}/report")
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/markdown')
    assert f'audit-{created["scan_id"]}.md' in response.headers['content-disposition']
    lines = response.text.splitlines()
    assert lines[0] == '# Отчёт аудита — вставленный код'
    assert 'SEC011' in response.text


def test_scan_report_csv_has_header_and_one_row_per_issue(clients):
    web = _web()
    created = _scan(web)
    response = web.get(f"/api/v1/audit/scans/{created['scan_id']}/report", params={'format': 'csv'})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    assert response.content.startswith('Правило;Уровень;Файл:строка;Сообщение;Фрагмент кода'.encode('utf-8'))
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert rows[0] == ['Правило', 'Уровень', 'Файл:строка', 'Сообщение', 'Фрагмент кода']
    assert len(rows) == len(created['issues']) + 1
    assert {row[0] for row in rows[1:]} == {'SEC010', 'SEC011', 'SEC012'}


def test_upload_and_scan_reports_share_one_renderer(clients, monkeypatch):
    from testpilot import db
    from testpilot.web import audit_api, audit_report, audit_report_api

    assert audit_api.render_audit_report is audit_report.render_audit_report
    assert audit_report_api.render_audit_report is audit_report.render_audit_report

    impact, _ = clients
    project = impact.post('/api/v1/projects', json={'name': 'Журнал аудитов', 'description': ''}).json()
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as zip_file:
        zip_file.writestr('Module.bsl', 'Процедура Пример()\nКонецПроцедуры\n')
    upload = impact.post(
        f"/api/v1/projects/{project['id']}/uploads",
        params={'filename': 'Sources.zip'},
        content=archive.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    ).json()
    with db._factory() as s:
        row = s.get(db.UploadRow, upload['id'])
        row.audit_result = {'total_issues': 0, 'issues_by_severity': {}, 'passed': True, 'issues': []}
        s.commit()

    web = _web()
    created = _scan(web)

    calls: list[tuple[str, str, str, int]] = []

    def spy(record_id, heading, created_at, result, format):
        calls.append((record_id, heading, format, result.get('total_issues')))
        return Response('rendered', media_type='text/plain')

    monkeypatch.setattr(audit_api, 'render_audit_report', spy)
    monkeypatch.setattr(audit_report_api, 'render_audit_report', spy)

    upload_report = web.get(f"/api/v1/audit/uploads/{upload['id']}/report", params={'format': 'csv'})
    scan_report = web.get(f"/api/v1/audit/scans/{created['scan_id']}/report", params={'format': 'md'})
    assert upload_report.status_code == 200, upload_report.text
    assert scan_report.status_code == 200, scan_report.text
    assert upload_report.text == scan_report.text == 'rendered'
    assert [call[0] for call in calls] == [upload['id'], created['scan_id']]
    assert [call[2] for call in calls] == ['csv', 'md']
    assert calls[1][3] == 3


def test_code_audit_row_keeps_only_report_columns_and_indexes_created_at():
    from testpilot.db import CodeAuditRow

    columns = [column.name for column in CodeAuditRow.__table__.columns]
    assert columns == EXPECTED_COLUMNS
    for forbidden in ('code', 'source_code', 'source_text', 'content', 'raw', 'text'):
        assert forbidden not in columns
    assert {index.name for index in CodeAuditRow.__table__.indexes} == {
        'ix_code_audit_sha256',
        'ix_code_audit_created_at',
    }
