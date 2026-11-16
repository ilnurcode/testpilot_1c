import csv
import io
from fastapi.testclient import TestClient
from zipfile import ZipFile



def _saved_audit(clients, *, with_audit=True, fragment='Запрос.Текст = "x;y" + Значение\nПродолжение'):
    from testpilot import db
    from testpilot.db import UploadRow

    impact, _ = clients
    project = impact.post('/api/v1/projects', json={'name': 'Отчётный проект', 'description': ''}).json()
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as zip_file:
        zip_file.writestr('Module.bsl', 'Процедура Пример()\nКонецПроцедуры\n')
    upload_response = impact.post(
        f"/api/v1/projects/{project['id']}/uploads",
        params={'filename': 'Sources.zip'},
        content=archive.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload_response.status_code == 200, upload_response.text
    upload = upload_response.json()
    if with_audit:
        result = {
            'total_files': 1,
            'total_issues': 1,
            'issues_by_severity': {'HIGH': 1},
            'issues_by_category': {'SECURITY': 1},
            'passed': False,
            'issues': [{
                'rule_id': 'SEC011',
                'severity': 'HIGH',
                'file_path': 'Documents/Order/ObjectModule.bsl',
                'line': 17,
                'message': 'Конкатенация текста запроса',
                'code_fragment': fragment,
            }],
        }
        with db._factory() as session:
            row = session.get(UploadRow, upload['id'])
            row.audit_result = result
            session.commit()
    from testpilot.web.api import app
    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    return web, upload['id']


def test_audit_report_markdown_contains_saved_summary_and_issue(clients):
    web, upload_id = _saved_audit(clients)
    response = web.get(f'/api/v1/audit/uploads/{upload_id}/report')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/markdown')
    assert '# Отчёт аудита — Отчётный проект / Sources.zip' in response.text
    assert '- Всего замечаний: 1' in response.text
    assert '- По уровням: HIGH: 1' in response.text
    assert '- Пройдено: нет' in response.text
    assert '| SEC011 | HIGH | Documents/Order/ObjectModule.bsl:17 |' in response.text


def test_audit_report_csv_escapes_delimiter_quotes_and_newlines(clients):
    fragment = 'Запрос.Текст = "x;y" + Значение\nПродолжение'
    web, upload_id = _saved_audit(clients, fragment=fragment)
    response = web.get(f'/api/v1/audit/uploads/{upload_id}/report?format=csv')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert len(rows) == 2
    assert rows[0] == ['Правило', 'Уровень', 'Файл:строка', 'Сообщение', 'Фрагмент кода']
    assert rows[1][4] == fragment


def test_audit_report_returns_404_without_saved_audit(clients):
    web, upload_id = _saved_audit(clients, with_audit=False)
    response = web.get(f'/api/v1/audit/uploads/{upload_id}/report')
    assert response.status_code == 404


def test_audit_report_rejects_unknown_format(clients):
    web, upload_id = _saved_audit(clients)
    response = web.get(f'/api/v1/audit/uploads/{upload_id}/report?format=xml')
    assert response.status_code == 400
