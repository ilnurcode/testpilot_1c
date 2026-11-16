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
    'upload_analyses',
    'affected',
    'analyses',
    'uploads',
    'audit',
    'projects',
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
