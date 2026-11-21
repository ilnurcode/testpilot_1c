"""Preflight плана: read-only ответ о настройках и блокерах. Требует PostgreSQL (clients)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sqlalchemy.orm import sessionmaker

from testpilot import db
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample
from test_ai_async import _wait


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
                          json={'web_url': web_url, 'odata_url': 'http://127.0.0.1:8088/odata/standard.odata'}).status_code == 200
    pkg = _package()
    assert c.post('/api/v1/impact-packages', json=pkg).status_code == 200
    return project, pkg['package_id']


def _local_suite(c, project_id: str, package_id: str, approve: bool = True, *, force_playwright: bool = False) -> str:
    created = c.post('/api/v1/ai/suites', json={
        'package_id': package_id, 'project_id': project_id, 'provider': 'local',
    })
    assert created.status_code == 200, created.text
    sid = created.json()['suite_id']
    if force_playwright:
        with _session() as s:
            row = s.get(db.AISuiteRow, sid)
            suggestion = dict(row.payload['suggestion'])
            proposal = dict(suggestion['proposals'][0])
            proposal.update(engine='playwright', cases=[{
                'steps': [{'action': 'wait', 'value': 1}],
                'assertions': [_section_assertion()],
            }])
            suggestion['proposals'] = [proposal]
            row.payload = {**row.payload, 'suggestion': suggestion}
            s.commit()
    if approve:
        assert c.post(f'/api/v1/ai/suites/{sid}/approve',
                      json={'reason': 'preflight', 'selected_proposals': [0]}).status_code == 200
    return sid


def _insert_suite(project_id: str, package_id: str, payload: dict) -> str:
    sid = str(uuid4())
    payload = {'suite_id': sid, 'created_at': _stamp(), 'status': 'approved', **payload}
    with _session() as s:
        from testpilot.ai import login_guard
        project=s.get(db.ProjectRow, project_id)
        if project is not None:
            payload['cooldown_key']=login_guard.cooldown_key(s, project, payload)
        s.add(db.AISuiteRow(id=sid, package_id=package_id, project_id=project_id, status='approved', payload=payload))
        s.commit()
    return sid


def _section_assertion():
    return {'id': 'section', 'scope': 'connectivity', 'check': 'web_section_visible',
            'engine': 'playwright', 'operator': 'exists', 'selector': {'section': 'Продажи'},
            'expected': {'type': 'boolean', 'value': True}}


def _mutating_payload() -> dict:
    return {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Изменяющая проверка', 'engine': 'playwright', 'mutating': True,
            'object_ids': ['document.x'], 'steps': ['записать'],
            'expected_result': 'ok', 'cases': [{'steps': [{'action': 'click', 'target': 'Записать'}],
                                              'assertions': [_section_assertion()]}],
        }]},
    }


def test_preflight_unknown_suite_is_404(clients):
    _, c = clients
    missing = str(uuid4())

    response = c.get(f'/api/v1/ai/suites/{missing}/preflight')

    assert response.status_code == 404, response.text


def test_preflight_without_approval_would_not_run(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    sid = _local_suite(c, project['id'], package_id, approve=False)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert body['would_run'] is False
    assert body['approved_indices'] == []
    assert any('нет утверждённых предложений' in warning for warning in body['warnings'])


def test_preflight_blocks_playwright_without_web_url(clients):
    _, c = clients
    project, package_id = _project(clients)  # без connection
    sid = _local_suite(c, project['id'], package_id, force_playwright=True)

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert body['would_run'] is False
    assert body['settings']['web_url_present'] is False
    playwright = next(item for item in body['engines'] if item['engine'] == 'playwright')
    assert 'web_url не настроен' in playwright['blocked_reason']


def test_preflight_warns_about_mutating_steps(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    sid = _insert_suite(project['id'], package_id, _mutating_payload())

    body = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()

    assert any('allow_mutations=true' in warning for warning in body['warnings'])
    assert body['engines'][0]['mutating'] is True


def test_preflight_warns_when_login_guard_would_block(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
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
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
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


def _strict_warnings(body):
    return [w for w in body['warnings'] if 'строгий режим' in w]


def test_preflight_strict_warning_only_when_mode_enabled(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    sid = _insert_suite(project['id'], package_id, _mutating_payload())

    quiet = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()
    assert _strict_warnings(quiet) == []

    with _session() as s:
        row = s.get(db.AISuiteRow, sid)
        row.payload = {**row.payload, 'strict_read_only': True}
        s.commit()
    strict = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()
    warnings = _strict_warnings(strict)
    assert warnings and 'строгий режим включён: клики будут отклонены' in warnings[0]


def test_run_without_approval_is_422_with_preflight_reason(clients):
    _, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    sid = _local_suite(c, project['id'], package_id, approve=False)

    response = c.post(f'/api/v1/ai/suites/{sid}/run-1c')

    assert response.status_code == 422, response.text
    assert response.json()['error']['message'] == 'нет утверждённых предложений: подтвердите план перед прогоном'


def test_run_playwright_without_web_url_is_422_with_preflight_reason(clients):
    _, c = clients
    project, package_id = _project(clients)
    sid = _local_suite(c, project['id'], package_id, force_playwright=True)

    response = c.post(f'/api/v1/ai/suites/{sid}/run-1c')

    assert response.status_code == 422, response.text
    # Подключения нет вовсе: blocker называет общую причину, а не половину (только web_url).
    assert response.json()['error']['message'] == (
        'Задайте подключение проекта: odata_url/web_url; env-профиль в прогоне не используется')


def test_run_ready_plan_still_executes(clients, monkeypatch):
    from testpilot.ai import executor as executor_module

    impact, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    assert impact.put(f"/api/v1/projects/{project['id']}/connection", json={
        'web_url': 'http://127.0.0.1:8088/DemoTrd',
        'odata_url': 'http://127.0.0.1:8088/odata/standard.odata',
    }).status_code == 200
    sid = _local_suite(c, project['id'], package_id)
    called = []
    monkeypatch.setattr(executor_module, 'execute_1c', lambda suite, profile, **kwargs: (
        called.append(suite['suite_id']) or ([{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright',
                                               'title': 'Вход', 'status': 'passed', 'message': 'Ок',
                                               'duration_seconds': 0.1, 'started_at': 'unit'}], [])
    ))

    response = c.post(f'/api/v1/ai/suites/{sid}/run-1c')

    assert response.status_code == 202, response.text
    assert _wait(c, sid)['status'] == 'completed'
    assert called == [sid]


def test_mutation_warning_does_not_block_preflight_or_run(clients):
    impact, c = clients
    project, package_id = _project(clients, web_url='http://127.0.0.1:8088/DemoTrd')
    assert impact.put(f"/api/v1/projects/{project['id']}/connection", json={
        'web_url': 'http://127.0.0.1:8088/DemoTrd',
        'odata_url': 'http://127.0.0.1:8088/odata/standard.odata',
    }).status_code == 200
    sid = _insert_suite(project['id'], package_id, _mutating_payload())

    preflight = c.get(f'/api/v1/ai/suites/{sid}/preflight').json()
    assert preflight['would_run'] is True, 'мутирующий шаг — предупреждение, не блокер'
    assert any('allow_mutations=true' in warning for warning in preflight['warnings'])

    response = c.post(f'/api/v1/ai/suites/{sid}/run-1c')

    assert response.status_code == 422, response.text
    message = response.json()['error']['message']
    assert 'потенциально изменяет данные' in message
    assert 'allow_mutations=true' in message
    assert 'нет утверждённых предложений' not in message
    assert 'web_url не настроен' not in message


def test_project_web_only_profile_never_uses_environment_connection(clients, monkeypatch):
    from testpilot.ai.api import _profile_for_suite
    impact, c = clients
    project, package_id = _project(clients)
    response = impact.put(f"/api/v1/projects/{project['id']}/connection", json={
        'web_url': 'http://127.0.0.1:8088/DemoTrd', 'odata_url': None,
        'auth_username': None, 'auth_password': None,
    })
    assert response.status_code == 200, response.text
    sid = _local_suite(c, project['id'], package_id)
    monkeypatch.setenv('TP_1C_ODATA_URL', 'https://unapproved.example.test/other')
    monkeypatch.setenv('TP_1C_USERNAME', 'different-env-user')
    monkeypatch.setenv('TP_1C_PASSWORD', 'different-env-password')
    monkeypatch.setenv('TP_1C_AUTHORIZATION', 'Bearer never-use-in-project-run')
    with _session() as session:
        profile = _profile_for_suite(session.get(db.AISuiteRow, sid),
                                     session.get(db.ProjectRow, project['id']), session)
    assert profile.web_url == 'http://127.0.0.1:8088/DemoTrd'
    assert profile.odata_url == ''
    assert profile.username is profile.password is profile.authorization is None
    response = impact.put(f"/api/v1/projects/{project['id']}/connection", json={
        'web_url': None, 'odata_url': None, 'auth_username': None, 'auth_password': None,
    })
    assert response.status_code == 200, response.text

    # План утверждён для цели, которой больше нет: это изменившееся согласие (409),
    # а не отсутствие настроек. Проверка не ослаблена — запуск не поставлен в очередь.
    stale = c.post(f'/api/v1/ai/suites/{sid}/run-1c')
    assert stale.status_code == 409, stale.text
    assert stale.json()['error']['message'] == (
        'Цель/подключение изменились после утверждения; требуется повторное согласование')
    assert c.get(f'/api/v1/ai/suites/{sid}/status').json()['status'] == 'approved', 'план не попал в очередь'

    # Повторное согласование уже с пустым подключением. Плану, которому нужна 1С, запуск
    # запрещён по конфигурации: профиль не строится, env-креды не подставляются.
    replanned = _local_suite(c, project['id'], package_id, force_playwright=True)
    refused = c.post(f'/api/v1/ai/suites/{replanned}/run-1c')
    assert refused.status_code == 422, refused.text
    assert refused.json()['error']['message'] == (
        'Задайте подключение проекта: odata_url/web_url; env-профиль в прогоне не используется')
    assert c.get(f'/api/v1/ai/suites/{replanned}/status').json()['status'] == 'approved'
    with _session() as session:
        with pytest.raises(HTTPException) as error:
            _profile_for_suite(session.get(db.AISuiteRow, replanned),
                               session.get(db.ProjectRow, project['id']), session)
    assert error.value.status_code == 422
    detail = str(error.value.detail)
    assert 'env-профиль в прогоне не используется' in detail
    for env_value in ('different-env-user', 'different-env-password', 'never-use-in-project-run'):
        assert env_value not in detail


def test_profile_missing_portable_key_maps_to_503(clients, monkeypatch, tmp_path):
    from fastapi import HTTPException
    from testpilot import secrets
    from testpilot.ai.api import _profile_for_suite

    key = tmp_path / 'portable.key'
    secrets.create_portable_key(key)
    monkeypatch.setenv(secrets.PROTECTOR_ENV, 'portable')
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(key))
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    impact, c = clients
    project, package_id = _project(clients)
    assert impact.put(f"/api/v1/projects/{project['id']}/connection", json={
        'web_url': 'http://127.0.0.1:8088/DemoTrd',
        'odata_url': 'http://127.0.0.1:8088/odata/standard.odata',
        'auth_username': 'Администратор',
        'auth_password': 'portable-pass',
    }).status_code == 200
    sid = _local_suite(c, project['id'], package_id)

    monkeypatch.delenv(secrets.PORTABLE_KEY_ENV)
    with _session() as session:
        with pytest.raises(HTTPException) as error:
            _profile_for_suite(session.get(db.AISuiteRow, sid),
                               session.get(db.ProjectRow, project['id']), session)
    assert error.value.status_code == 503
    assert 'portable-pass' not in str(error.value.detail)
