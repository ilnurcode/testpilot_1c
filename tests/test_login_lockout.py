"""Предохранитель от блокировки учётной записи 1С: лимит неудачных входов за окно времени."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from testpilot import db
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def _package():
    return analyze(sample(1), sample(2), {'edge_types': ['calls', 'uses', 'belongs_to', 'subscribes_to'],
                                          'direction': 'impact', 'max_depth': 10})[1]


def _project_and_suite(clients):
    impact, c = clients
    project = impact.post('/api/v1/projects', json={'name': 'Лимит входов', 'description': ''}).json()
    pkg = _package()
    assert c.post('/api/v1/impact-packages', json=pkg).status_code == 200
    created = c.post('/api/v1/ai/suites', json={
        'package_id': pkg['package_id'], 'project_id': project['id'], 'provider': 'local',
    })
    assert created.status_code == 200, created.text
    suite = created.json()
    assert c.post(f"/api/v1/ai/suites/{suite['suite_id']}/approve",
                  json={'reason': 'Проверка лимита', 'selected_proposals': [0]}).status_code == 200
    return project, suite, pkg['package_id']


def _stamp(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace('+00:00', 'Z')


def _seed(project_id: str, package_id: str, minutes_ago: int, success: bool = False) -> str:
    suite_id = str(uuid4())
    if success:
        results = [{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Вход',
                    'status': 'passed', 'message': 'Раздел открыт',
                    'login': {'stage': 'after_click', 'form_visible_after': False, 'clicked': True,
                              'user_filled': True, 'password_filled': False}}]
    else:
        results = [{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Вход',
                    'status': 'error', 'message': 'Вход в 1С не выполнен: форма логина осталась на экране',
                    'login': {'stage': 'after_click', 'form_visible_after': True, 'clicked': True,
                              'user_filled': True, 'password_filled': False}}]
    payload = {'suite_id': suite_id, 'created_at': _stamp(minutes_ago), 'status': 'completed',
               'results': results, 'counts': {'passed': int(success), 'failed': 0, 'error': int(not success), 'skipped': 0}}
    with db._factory() as s:
        s.add(db.AISuiteRow(id=suite_id, package_id=package_id, project_id=project_id,
                            status='completed', payload=payload))
        s.commit()
    return suite_id


def _run(clients, suite, monkeypatch):
    from testpilot.ai import executor as executor_module

    calls = []
    monkeypatch.setattr(executor_module, 'execute_1c', lambda target, profile: (
        calls.append(target['suite_id']) or ([{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright',
                                               'title': 'Вход', 'status': 'passed', 'message': 'Ок',
                                               'duration_seconds': 0.1, 'started_at': 'unit'}], [])
    ))
    _, c = clients
    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    return response, calls


def test_three_failures_block_run_without_browser(clients, monkeypatch):
    project, suite, package_id = _project_and_suite(clients)
    for minutes_ago in (25, 15, 5):
        _seed(project['id'], package_id, minutes_ago)

    response, calls = _run(clients, suite, monkeypatch)

    assert response.status_code == 429, response.text
    message = response.json()['error']['message']
    assert 'Вход в 1С приостановлен: 3 неудачных попыток за 30 минут' in message
    assert 'защита от блокировки учётной записи' in message
    assert calls == [], 'исполнитель и браузер не должны запускаться'


def test_two_failures_allow_run(clients, monkeypatch):
    project, suite, package_id = _project_and_suite(clients)
    for minutes_ago in (20, 10):
        _seed(project['id'], package_id, minutes_ago)

    response, calls = _run(clients, suite, monkeypatch)

    assert response.status_code == 200, response.text
    assert calls == [suite['suite_id']]


def test_old_failures_outside_window_allow_run(clients, monkeypatch):
    project, suite, package_id = _project_and_suite(clients)
    for minutes_ago in (60, 50, 40):
        _seed(project['id'], package_id, minutes_ago)

    response, calls = _run(clients, suite, monkeypatch)

    assert response.status_code == 200, response.text
    assert calls == [suite['suite_id']]


def test_successful_login_resets_failure_series(clients, monkeypatch):
    project, suite, package_id = _project_and_suite(clients)
    for minutes_ago in (25, 20, 15):
        _seed(project['id'], package_id, minutes_ago)
    _seed(project['id'], package_id, 5, success=True)

    response, calls = _run(clients, suite, monkeypatch)

    assert response.status_code == 200, response.text
    assert calls == [suite['suite_id']]


@pytest.mark.parametrize('raw', ['0', '1441', 'abc'])
def test_cooldown_minutes_out_of_range_fails_start(raw):
    from testpilot import config

    with pytest.raises(ValueError):
        config.int_env('TP_1C_LOGIN_COOLDOWN_MINUTES', 30, 1, 1440, raw=raw)


@pytest.mark.parametrize('raw', ['0', '11', 'abc'])
def test_max_failures_out_of_range_fails_start(raw):
    from testpilot import config

    with pytest.raises(ValueError):
        config.int_env('TP_1C_LOGIN_MAX_FAILURES', 3, 1, 10, raw=raw)
