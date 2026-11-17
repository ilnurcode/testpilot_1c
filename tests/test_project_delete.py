"""Удаление проекта: подтверждение, каскад и атомарность."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from zipfile import ZipFile

from fastapi.testclient import TestClient

from testpilot.contracts import ImpactPackage, checksum

DELETED_KEYS = {
    'evidence',
    'suites',
    'packages',
    'tests',
    'coverage',
    'priorities',
    'results',
    'defects',
    'runs',
    'upload_analyses',
    'affected',
    'analyses',
    'uploads',
    'audit',
    'projects',
}

# Колонки-ссылки на удаляемые сущности. Проверка обходит все таблицы метаданных,
# поэтому новая таблица со ссылкой без обработки в каскаде сразу даст сироту.
REFERENCE_COLUMNS = {
    'project_id',
    'suite_id',
    'package_id',
    'upload_id',
    'analysis_id',
    'test_id',
    'run_id',
    'plan_id',
    'baseline_id',
    'entity_id',
}


def _package(suffix: str) -> dict:
    body = {
        'schema_version': '1.0',
        'package_id': f'delete-project-{suffix}',
        'configuration_id': 'delete-project-stand',
        'base_version': '1.0',
        'target_version': '2.0',
        'analysis_id': f'delete-project-analysis-{suffix}',
        'created_at': datetime.now(timezone.utc),
        'synthetic': True,
        'changed_objects': [{'object_id': 'catalog.номенклатура', 'status': 'added', 'properties': []}],
        'affected_objects': ['catalog.номенклатура'],
        'dependency_paths': [],
        'warnings': [],
    }
    model = ImpactPackage.model_construct(**body, checksum='0' * 64)
    data = model.model_dump(mode='json', exclude={'checksum'})
    package = {**data, 'checksum': checksum(data)}
    ImpactPackage.model_validate(package)
    return package


def _project(impact, name='Мусорный проект') -> dict:
    response = impact.post('/api/v1/projects', json={'name': name, 'description': ''})
    assert response.status_code == 200, response.text
    return response.json()


def _upload(impact, project_id: str) -> dict:
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as zip_file:
        zip_file.writestr('Module.bsl', 'Процедура Пример()\nКонецПроцедуры\n')
    response = impact.post(
        f'/api/v1/projects/{project_id}/uploads',
        params={'filename': 'Sources.zip'},
        content=archive.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _suite(c, project_id: str, package: dict) -> dict:
    assert c.post('/api/v1/impact-packages', json=package).status_code == 200
    response = c.post('/api/v1/ai/suites', json={
        'package_id': package['package_id'],
        'project_id': project_id,
        'provider': 'local',
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_delete_requires_confirm(clients):
    impact, _ = clients
    project = _project(impact)

    response = impact.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 409
    assert f"подтвердите удаление: передайте confirm={project['id']}" == response.json()['error']['message']
    assert impact.get(f"/api/v1/projects/{project['id']}").status_code == 200


def test_delete_rejects_wrong_confirm(clients):
    impact, _ = clients
    project = _project(impact)

    response = impact.delete(f"/api/v1/projects/{project['id']}", params={'confirm': 'not-this-project'})

    assert response.status_code == 409
    assert 'подтвердите удаление' in response.json()['error']['message']
    assert impact.get(f"/api/v1/projects/{project['id']}").status_code == 200


def test_delete_unknown_project_is_404(clients):
    impact, _ = clients
    response = impact.delete('/api/v1/projects/00000000-0000-0000-0000-000000000000',
                             params={'confirm': '00000000-0000-0000-0000-000000000000'})
    assert response.status_code == 404


def test_delete_removes_project_and_related_records(clients):
    impact, c = clients
    project = _project(impact)
    upload = _upload(impact, project['id'])
    suite = _suite(c, project['id'], _package('cascade'))
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    assert web.get(f"/api/v1/audit/uploads/{upload['id']}").status_code == 404

    response = impact.delete(f"/api/v1/projects/{project['id']}", params={'confirm': project['id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['project_id'] == project['id']
    assert set(body['deleted']) == DELETED_KEYS
    assert body['deleted']['uploads'] == 1
    assert body['deleted']['suites'] == 1
    assert body['deleted']['packages'] == 1
    assert body['deleted']['projects'] == 1

    assert impact.get(f"/api/v1/projects/{project['id']}").status_code == 404
    assert c.get(f"/api/v1/ai/suites/{suite['suite_id']}").status_code == 404
    assert web.get(f"/api/v1/audit/uploads/{upload['id']}").status_code == 404


def test_delete_keeps_neighbour_project(clients):
    impact, c = clients
    left = _project(impact, 'Левый')
    right = _project(impact, 'Правый')
    _upload(impact, left['id'])
    _upload(impact, right['id'])
    right_suite = _suite(c, right['id'], _package('neighbour'))

    assert impact.delete(f"/api/v1/projects/{left['id']}", params={'confirm': left['id']}).status_code == 200

    assert impact.get(f"/api/v1/projects/{left['id']}").status_code == 404
    assert impact.get(f"/api/v1/projects/{right['id']}").status_code == 200
    assert len(impact.get(f"/api/v1/projects/{right['id']}").json()['uploads']) == 1
    assert c.get(f"/api/v1/ai/suites/{right_suite['suite_id']}").status_code == 200


def test_delete_keeps_package_shared_with_another_project(clients):
    impact, c = clients
    left = _project(impact, 'Левый с пакетом')
    right = _project(impact, 'Правый с пакетом')
    package = _package('shared')
    _suite(c, left['id'], package)
    _suite(c, right['id'], package)

    body = impact.delete(f"/api/v1/projects/{left['id']}", params={'confirm': left['id']}).json()

    assert body['deleted']['suites'] == 1
    assert body['deleted']['packages'] == 0
    rows = c.get('/api/v1/ai/suites', params={'project_id': right['id']}).json()
    assert len(rows) == 1


def test_delete_leaves_code_audit_journal_untouched(clients):
    impact, _ = clients
    from testpilot import db
    from testpilot.web.api import app

    web = TestClient(app)
    web.auth = ('expert', 'test-password-only')
    scan = web.post('/api/v1/audit/scan', json={'code': 'ОтключитьБезопасныйРежим(Истина);'})
    assert scan.status_code == 200, scan.text
    scan_id = scan.json()['scan_id']
    project = _project(impact, 'Проект рядом с журналом')

    response = impact.delete(f"/api/v1/projects/{project['id']}", params={'confirm': project['id']})

    assert response.status_code == 200
    assert response.json()['deleted']['projects'] == 1
    with db._factory() as session:
        assert session.get(db.CodeAuditRow, scan_id) is not None
    assert web.get(f'/api/v1/audit/scans/{scan_id}').status_code == 200


def _run_input(suite: dict, project_configuration: str) -> dict:
    test_ids = list((suite.get('catalog_test_ids') or {}).values())
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return {
        'run_id': f"ai-run-{suite['suite_id']}",
        'configuration_id': project_configuration,
        'version': '2.0',
        'synthetic': suite['synthetic'],
        'results': [
            {'test_id': tid, 'status': 'passed', 'duration_seconds': 1.0,
             'started_at': now, 'finished_at': now}
            for tid in test_ids
        ],
        'defects': [],
    }


def _orphans(session, ids) -> list[tuple[str, str, int]]:
    from sqlalchemy import func, select as sql_select

    from testpilot import db

    found = []
    for table in db.Base.metadata.sorted_tables:
        for column in table.columns:
            if column.name not in REFERENCE_COLUMNS:
                continue
            leftover = session.execute(
                sql_select(func.count()).select_from(table).where(column.in_(ids))
            ).scalar()
            if leftover:
                if table.name == 'audit' and column.name == 'entity_id':
                    continue
                found.append((table.name, column.name, leftover))
    return found


def test_delete_leaves_no_orphans_in_referencing_tables(clients):
    import uuid

    from testpilot import db

    impact, c = clients
    project = _project(impact, 'Проект с прогоном')
    upload = _upload(impact, project['id'])
    package = _package(f'orphans-{uuid.uuid4().hex[:8]}')
    suite = _suite(c, project['id'], package)
    approved = c.post(
        f"/api/v1/ai/suites/{suite['suite_id']}/approve",
        json={'reason': 'Проверка сирот', 'selected_proposals': [0]},
    )
    assert approved.status_code == 200, approved.text
    suite_payload = c.get(f"/api/v1/ai/suites/{suite['suite_id']}").json()
    test_ids = list(suite_payload['catalog_test_ids'].values())
    assert test_ids

    history = c.post('/api/v1/history', json=_run_input(suite_payload, package['configuration_id']))
    assert history.status_code == 200, history.text

    deleted = impact.delete(f"/api/v1/projects/{project['id']}", params={'confirm': project['id']})
    assert deleted.status_code == 200, deleted.text
    counts = deleted.json()['deleted']
    assert counts['tests'] == len(test_ids)
    assert counts['runs'] == 1
    assert counts['results'] == len(test_ids)
    assert counts['coverage'] == len({oid for oid in suite_payload['suggestion']['proposals'][0]['object_ids']})

    ids = {project['id'], upload['id'], suite['suite_id'], package['package_id'], *test_ids,
           f"ai-run-{suite['suite_id']}"}
    with db._factory() as session:
        assert _orphans(session, ids) == []
        audit_rows = session.execute(
            __import__('sqlalchemy').select(db.AuditRow.action).where(db.AuditRow.entity_id == project['id'])
        ).scalars().all()
    assert audit_rows == ['delete_project']


def _run_count(client, url):
    from sqlalchemy import event

    from testpilot import db

    statements = []

    def counter(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db._engine, 'before_cursor_execute', counter)
    try:
        response = client.get(url)
        assert response.status_code == 200, response.text
    finally:
        event.remove(db._engine, 'before_cursor_execute', counter)
    return len(statements), response.json()


def test_projects_list_reports_last_run_null_and_after_execution(clients, monkeypatch):
    from testpilot.ai import executor as executor_module

    impact, c = clients
    project = _project(impact, 'Проект без прогонов')
    rows = impact.get('/api/v1/projects').json()
    mine = next(row for row in rows if row['id'] == project['id'])
    assert mine['last_run'] is None
    assert mine['upload_count'] == 0 and mine['latest_upload'] is None

    p = _package('last-run')
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    suite = c.post('/api/v1/ai/suites', json={'package_id': p['package_id'], 'project_id': project['id'], 'provider': 'local'})
    assert suite.status_code == 200, suite.text
    monkeypatch.setattr(executor_module, 'execute_1c', lambda suite, profile: ([
        {'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Навигация',
         'status': 'failed', 'message': 'Расхождение на стенде', 'duration_seconds': 0.4, 'started_at': 'unit'},
        {'proposal_index': 0, 'case_index': 1, 'engine': 'playwright', 'title': 'Навигация',
         'status': 'passed', 'message': 'Ок', 'duration_seconds': 0.1, 'started_at': 'unit'},
    ], []))
    assert c.post(f"/api/v1/ai/suites/{suite.json()['suite_id']}/approve",
                  json={'reason': 'Приёмка last_run', 'selected_proposals': [0]}).status_code == 200
    run = c.post(f"/api/v1/ai/suites/{suite.json()['suite_id']}/run-1c")
    assert run.status_code == 200, run.text

    mine = next(row for row in impact.get('/api/v1/projects').json() if row['id'] == project['id'])
    assert mine['last_run']['suite_id'] == suite.json()['suite_id']
    assert mine['last_run']['counts'] == {'passed': 1, 'failed': 1, 'error': 0, 'skipped': 0}
    assert mine['last_run']['status'] == 'completed'
    assert mine['last_run']['created_at']
    assert mine['last_run']['finished_at']


def test_projects_list_shows_newest_run_as_last_run(clients, monkeypatch):
    from testpilot.ai import executor as executor_module

    impact, c = clients
    project = _project(impact, 'Проект с rerun')
    p = _package('rerun')
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    suite = c.post('/api/v1/ai/suites', json={'package_id': p['package_id'], 'project_id': project['id'], 'provider': 'local'}).json()
    assert c.post(f"/api/v1/ai/suites/{suite['suite_id']}/approve",
                  json={'reason': 'Повтор', 'selected_proposals': [0]}).status_code == 200
    monkeypatch.setattr(executor_module, 'execute_1c', lambda s, profile: ([
        {'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Навигация',
         'status': 'passed', 'message': 'Ок', 'duration_seconds': 0.1, 'started_at': 'unit'},
    ], []))
    assert c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c").status_code == 200
    rerun = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/rerun")
    assert rerun.status_code == 200, rerun.text

    rows = impact.get('/api/v1/projects').json()
    mine = next(row for row in rows if row['id'] == project['id'])
    assert mine['last_run']['suite_id'] == rerun.json()['suite_id']
    assert mine['last_run']['suite_id'] != suite['suite_id']


def test_projects_list_last_run_tiebreak_by_id(clients):
    import time

    from testpilot import db

    impact, c = clients
    project = _project(impact, 'Проект с равным временем')
    pkg = _package('tie')
    assert c.post('/api/v1/impact-packages', json=pkg).status_code == 200
    stamp = '2026-01-01T00:00:00.000000Z'
    from sqlalchemy import select as sql_select

    with db._factory() as s:
        for suite_id in ('aaaaaaaa-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000002'):
            s.add(db.AISuiteRow(id=suite_id, package_id=pkg['package_id'], project_id=project['id'], status='completed',
                                payload={'suite_id': suite_id, 'counts': {'passed': 1, 'failed': 0, 'error': 0, 'skipped': 0},
                                         'finished_at': stamp, 'created_at': stamp, 'approval': {'selected_proposals': [0]}}))
        s.commit()
        for row in s.scalars(sql_select(db.AISuiteRow).where(db.AISuiteRow.project_id == project['id'])):
            row.created_at = stamp
        s.commit()

    mine = next(row for row in impact.get('/api/v1/projects').json() if row['id'] == project['id'])
    assert mine['last_run']['suite_id'] == 'bbbbbbbb-0000-0000-0000-000000000002'


def test_projects_list_has_no_n_plus_one(clients):
    impact, _ = clients
    single = _project(impact, 'Один проект')
    one_query, one_rows = _run_count(impact, '/api/v1/projects')
    assert len(one_rows) >= 1

    for index in range(4):
        _project(impact, f'Проект {index}')
    five_queries, five_rows = _run_count(impact, '/api/v1/projects')

    assert len(five_rows) == len(one_rows) + 4
    assert five_queries == one_query, f'число запросов выросло: {one_query} -> {five_queries}'
