"""B2: демо-прогон не держит транзакцию и suite-lock во время длинного шага.

Наблюдаемые инварианты (реальный PostgreSQL, реальные миграции):
* во время «браузерного» шага строка suite не заблокирована (FOR UPDATE NOWAIT проходит)
  и нет соединения в состоянии idle in transaction;
* параллельные status/cancel/run-demo отвечают сразу, а не после прогона;
* повтор и параллельный запуск не создают дублей, сбой и истёкший lease не оставляют running.
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text

from testpilot import db
from testpilot.ai import api as ai_api
from testpilot.ai import worker
from test_ai import package


@pytest.fixture
def demo_clients(monkeypatch, request):
    # Тот же fixture, что и в остальных прогонах, но без фонового воркера:
    # во время шага в БД не должно быть чужих соединений.
    with monkeypatch.context() as setup:
        setup.setattr(worker, 'start', lambda factory: None)
        clients = request.getfixturevalue('clients')
    yield clients


def _probe_suite_row(sid):
    """Снимок: есть ли idle in transaction и проходит ли блокировка строки без ожидания."""
    engine = db.engine()
    with engine.connect() as connection:
        idle = connection.execute(text(
            "select count(*) from pg_stat_activity where datname = current_database() "
            "and state like 'idle in transaction%' and pid <> pg_backend_pid()")).scalar()
    nowait = 'granted'
    try:
        with engine.connect() as connection:
            connection.execute(text('select 1 from ti_ai_suites where id = :sid for update nowait'),
                               {'sid': sid})
            connection.rollback()
    except Exception as exc:  # psycopg.errors.LockNotAvailable
        nowait = type(exc).__name__
    return {'idle_in_transaction': idle, 'for_update_nowait': nowait}


def _demo_suite(clients, monkeypatch, *, delay=1.5, probe=None, shots=(), error=None, business=None):
    """Утверждённый демо-прогон с подменой длинного шага на сон."""
    impact, c = clients
    payload = package()
    assert c.post('/api/v1/impact-packages', json=payload).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'B2 lock', 'description': ''}).json()
    created = c.post('/api/v1/ai/suites', json={
        'package_id': payload['package_id'], 'project_id': project['id'], 'provider': 'demo'})
    assert created.status_code == 200, created.text
    sid = created.json()['suite_id']
    approved = c.post(f'/api/v1/ai/suites/{sid}/approve',
                      json={'reason': 'B2 lock scope', 'selected_proposals': [0]})
    assert approved.status_code == 200, approved.text

    calls = []

    def execute(suite, authorization):
        from testpilot.contracts import now
        calls.append(probe(sid) if probe is not None else None)
        time.sleep(delay)
        if error is not None:
            raise error
        started = now()
        result = {'proposal_index': 0, 'case_index': 0, 'title': 'lock scope', 'engine': 'playwright',
                  'input': {}, 'expected': 2, 'actual': 2, 'started_at': started,
                  'finished_at': now(), 'duration_seconds': delay, 'status': 'passed',
                  'synthetic': True, 'message': 'stub'}
        if business is not None:
            result.update(business_checked=True, business_success=business)
        return [result], list(shots)

    monkeypatch.setattr(ai_api, 'execute', execute)
    return sid, calls


def _row(sid):
    with db._factory() as session:
        return session.get(db.AISuiteRow, sid)


def _scalar(sql, **params):
    with db._factory() as session:
        return session.execute(text(sql), params).scalar()


def test_demo_run_holds_no_transaction_or_suite_lock_during_long_step(demo_clients, monkeypatch):
    impact, c = demo_clients
    sid, calls = _demo_suite(demo_clients, monkeypatch, delay=1.5, probe=_probe_suite_row)

    box = {}

    def run():
        box['response'] = c.post(f'/api/v1/ai/suites/{sid}/run-demo')

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.5)
    started = time.perf_counter()
    state = c.get(f'/api/v1/ai/suites/{sid}/status')
    status_seconds = time.perf_counter() - started
    started = time.perf_counter()
    cancelled = c.post(f'/api/v1/ai/suites/{sid}/cancel')
    cancel_seconds = time.perf_counter() - started
    started = time.perf_counter()
    second = c.post(f'/api/v1/ai/suites/{sid}/run-demo')
    second_seconds = time.perf_counter() - started
    thread.join(timeout=30)

    assert calls, 'длинный шаг не вызывался'
    assert calls[0]['for_update_nowait'] == 'granted', calls[0]
    assert calls[0]['idle_in_transaction'] == 0, calls[0]
    assert state.status_code == 200 and state.json()['status'] == 'running'
    assert status_seconds < 1.0, status_seconds
    assert cancel_seconds < 1.0, cancel_seconds
    assert cancelled.status_code == 409
    assert second.status_code == 409 and second_seconds < 1.0
    assert box['response'].status_code == 200, box['response'].text
    data = box['response'].json()
    assert data['status'] == 'completed' and data['counts']['passed'] == 1
    row = _row(sid)
    assert row.status == 'completed' and row.lease_expires_at is None
    assert row.payload['status'] == 'completed' and len(row.payload['results']) == 1
    assert 'demo_claim' not in row.payload


def test_parallel_demo_run_is_rejected_by_short_claim_without_duplicates(demo_clients, monkeypatch):
    impact, c = demo_clients
    png = b'\x89PNG\r\n\x1a\n' + b'0' * 32
    sid, calls = _demo_suite(demo_clients, monkeypatch, delay=1.2, shots=[(0, png)])
    answers = []

    def run():
        answers.append(c.post(f'/api/v1/ai/suites/{sid}/run-demo'))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(response.status_code for response in answers) == [200, 409], [
        (r.status_code, r.text[:200]) for r in answers]
    assert len(calls) == 1, 'длинный шаг не должен запускаться дважды'
    assert _scalar('select count(*) from ti_ai_evidence where suite_id = :sid', sid=sid) == 1
    assert _scalar('select count(*) from ti_runs where id = :rid', rid='ai-run-' + sid) == 1
    row = _row(sid)
    assert row.status == 'completed' and len(row.payload['results']) == 1
    repeat = c.post(f'/api/v1/ai/suites/{sid}/run-demo')
    assert repeat.status_code == 200
    assert repeat.json()['results'] == row.payload['results']
    assert _scalar('select count(*) from ti_ai_evidence where suite_id = :sid', sid=sid) == 1


def test_demo_failure_is_terminal_and_does_not_leave_lease(demo_clients, monkeypatch):
    impact, c = demo_clients
    sid, _calls = _demo_suite(demo_clients, monkeypatch, delay=0.05,
                              error=RuntimeError('browser crashed'))
    with pytest.raises(RuntimeError):
        c.post(f'/api/v1/ai/suites/{sid}/run-demo')
    row = _row(sid)
    assert row.status == 'error', row.payload.get('message')
    assert row.lease_expires_at is None
    assert row.payload['status'] == 'error' and 'browser crashed' in row.payload['message']
    # Сбой не оставляет прогон в running: повтор требует явного rerun, как у очереди.
    assert c.post(f'/api/v1/ai/suites/{sid}/run-demo').status_code == 409


def test_expired_demo_lease_is_reaped_and_late_result_is_fenced(demo_clients, monkeypatch):
    impact, c = demo_clients
    holder = {}

    def expire():
        with db._factory() as session:
            row = session.get(db.AISuiteRow, holder['sid'])
            row.lease_expires_at = '1'
            session.commit()
        return worker.recover_interrupted(db._factory)

    sid, _calls = _demo_suite(demo_clients, monkeypatch, delay=0.6)
    holder['sid'] = sid
    answers = {}

    def run():
        answers['response'] = c.post(f'/api/v1/ai/suites/{sid}/run-demo')

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.25)
    assert expire() == 1
    thread.join(timeout=30)
    assert answers['response'].status_code == 409, answers['response'].text
    row = _row(sid)
    assert row.status == 'interrupted' and row.lease_expires_at is None
    assert row.payload['status'] == 'interrupted'

def test_demo_finish_aggregates_business_success(demo_clients, monkeypatch):
    impact, c = demo_clients
    sid, _calls = _demo_suite(demo_clients, monkeypatch, delay=0.01, business=True)
    response = c.post(f'/api/v1/ai/suites/{sid}/run-demo')

    assert response.status_code == 200, response.text
    assert response.json()['business_success'] is True
    assert _row(sid).payload['business_success'] is True


def test_demo_without_business_checks_reports_none(demo_clients, monkeypatch):
    impact, c = demo_clients
    sid, _calls = _demo_suite(demo_clients, monkeypatch, delay=0.01)
    response = c.post(f'/api/v1/ai/suites/{sid}/run-demo')

    assert response.status_code == 200, response.text
    assert response.json()['business_success'] is None
