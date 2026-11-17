"""Preflight плана: read-only ответ о настройках и блокерах. Требует PostgreSQL (clients)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from testpilot import db
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def _package():
    return analyze(sample(1), sample(2), {'edge_types': ['calls', 'uses', 'belongs_to', 'subscribes_to'],
                                          'direction': 'impact', 'max_depth': 10})[1]


def _session():
    return sessionmaker(db.engine(), expire_on_commit=False)()


def _stamp(minutes_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace('+00:00', 'Z')


def _project(clients, *, web_url: str | None = None):
    impact, c = clients
    project = impact.post('/api/v1/projects', json={'name': 'Preflight', 'description': ''}).json()
    if web_url:
        assert impact.put(f"/api/v1/projects/{project['id']}/connection",
                          json={'web_url': web_url, 'odata_url': 'https://1c.example.test/odata/standard.odata'}).status_code == 200
    pkg = _package()
    assert c.post('/api/v1/impact-packages', json=pkg).status_code == 200
    return project, pkg['package_id']


def _local_suite(c, project_id: str, package_id: str, approve: bool = True) -> str:
    created = c.post('/api/v1/ai/suites', json={
        'package_id': package_id, 'project_id': project_id, 'provider': 'local',
    })
    assert created.status_code == 200, created.text
    sid = created.json()['suite_id']
    if approve:
        assert c.post(f'/api/v1/ai/suites/{sid}/approve',
                      json={'reason': 'preflight', 'selected_proposals': [0]}).status_code == 200
    return sid


def _insert_suite(project_id: str, package_id: str, payload: dict) -> str:
    sid = str(uuid4())
    payload = {'suite_id': sid, 'created_at': _stamp(), 'status': 'approved', **payload}
    with _session() as s:
        s.add(db.AISuiteRow(id=sid, package_id=package_id, project_id=project_id, status='approved', payload=payload))
        s.commit()
    return sid


def _mutating_payload() -> dict:
    return {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Изменяющая проверка', 'engine': 'playwright', 'mutating': True,
            'object_ids': ['document.x'], 'steps': ['записать'],
            'expected_result': 'ok', 'cases': [{'steps': [{'action': 'click', 'target': 'Записать'}]}],
        }]},
    }


def test_preflight_unknown_suite_is_404(clients):
    _, c = clients
    missing = str(uuid4())

    response = c.get(f'/api/v1/ai/suites/{missing}/preflight')

    assert response.status_code == 404, response.text


def test_preflight_without_approval_would_not_run(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://1c.example.test/DemoTrd')
    sid = _local_suite(c, project['id'], package_id, approve=False)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert body['would_run'] is False
    assert body['approved_indices'] == []
    assert any('нет утверждённых предложений' in warning for warning in body['warnings'])


def test_preflight_blocks_playwright_without_web_url(clients):
    _, c = clients
    project, package_id = _project(clients)  # без connection
    sid = _local_suite(c, project['id'], package_id)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert body['would_run'] is False
    assert body['settings']['web_url_present'] is False
    playwright = next(item for item in body['engines'] if item['engine'] == 'playwright')
    assert 'web_url не настроен' in playwright['blocked_reason']


def test_preflight_warns_about_mutating_steps(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://1c.example.test/DemoTrd')
    sid = _insert_suite(project['id'], package_id, _mutating_payload())

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert any('allow_mutations=true' in warning for warning in body['warnings'])
    assert body['engines'][0]['mutating'] is True


def test_preflight_warns_when_login_guard_would_block(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://1c.example.test/DemoTrd')
    for minutes_ago in (25, 15, 5):
        _insert_suite(project['id'], package_id, {
            'status': 'completed', 'approval': {'selected_proposals': [0]},
            'suggestion': {'proposals': [{'title': 'Вход', 'engine': 'playwright', 'object_ids': ['document.x'],
                                          'steps': ['вход'], 'expected_result': 'ok', 'cases': [{'steps': []}]}]},
            'results': [{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Вход',
                         'status': 'error', 'message': 'Вход в 1С не выполнен',
                         'login': {'stage': 'after_click', 'form_visible_after': True, 'clicked': True,
                                   'user_filled': True, 'password_filled': False}}],
            'created_at': _stamp(minutes_ago),
        })
    sid = _local_suite(c, project['id'], package_id)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert any('Вход в 1С приостановлен' in warning for warning in body['warnings']), body['warnings']


def test_preflight_ready_plan_reports_runtime_values(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://host.docker.internal:8088/DemoTrd')
    sid = _local_suite(c, project['id'], package_id)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert body['would_run'] is True
    assert body['settings'] == {'web_url_present': True, 'odata_url_present': True,
                                'username_present': False, 'has_password': False}
    assert body['runtime']['grace_seconds'] == 30
    assert body['runtime']['login_wait_seconds'] == 25
    assert body['runtime']['login_submit'] in {'click', 'enter'}
    assert body['runtime']['login_cooldown_minutes'] == 30
    assert body['runtime']['login_max_failures'] == 3
    assert body['runtime']['audit_max_issues'] >= 100
    assert body['runtime']['allow_mutations_mode'] is False
    assert body['runtime']['strict_read_only_mode'] is False
