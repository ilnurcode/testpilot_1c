"""Worker checkpoints/recovery through API consumers on marker-checked disposable PostgreSQL."""
from __future__ import annotations

import base64
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from testpilot import db
from testpilot.ai import worker
from test_ai import package

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jp1sAAAAASUVORK5CYII='
)


@pytest.fixture
def recovery_run(monkeypatch, request):
    # Reuse the normal migrated-schema/API fixture, but drive this worker explicitly.
    with monkeypatch.context() as setup:
        setup.setattr(worker, 'start', lambda factory: None)
        clients = request.getfixturevalue('clients')
    factory = db._factory
    impact, api = clients
    payload = package()
    assert api.post('/api/v1/impact-packages', json=payload).status_code == 200
    response = impact.post('/api/v1/projects', json={'name': 'Recovery consumer', 'description': ''})
    assert response.status_code == 200, response.text
    response = api.post('/api/v1/ai/suites', json={
        'package_id': payload['package_id'], 'project_id': response.json()['id'], 'provider': 'demo',
    })
    assert response.status_code == 200, response.text
    sid = response.json()['suite_id']
    with factory() as session:
        row = session.get(db.AISuiteRow, sid)
        data = dict(row.payload)
        suggestion = dict(data['suggestion'])
        proposal = dict(suggestion['proposals'][0])
        proposal.update(engine='manual', cases=[{'assertions': [{
            'id': 'manual-recovery', 'scope': 'manual', 'check': 'manual', 'engine': 'manual',
            'operator': 'manual', 'requirement': 'Disposable recovery consumer', 'source_ref': 'ТЗ',
        }]}])
        suggestion['proposals'] = [proposal]
        data['suggestion'] = suggestion
        row.payload = data
        session.commit()
    response = api.post(f'/api/v1/ai/suites/{sid}/approve', json={
        'reason': 'Recovery test only', 'selected_proposals': [0],
    })
    assert response.status_code == 200, response.text
    response = api.post(f'/api/v1/ai/suites/{sid}/run-1c')
    assert response.status_code == 202, response.text
    claim = worker.claim(factory)
    assert claim is not None and claim['suite_id'] == sid
    try:
        yield api, factory, claim
    finally:
        worker.stop()


def _payload(api, claim):
    response = api.get(f"/api/v1/ai/suites/{claim['suite_id']}")
    assert response.status_code == 200, response.text
    return response.json()


def _result(step_index=None):
    result = {'proposal_index': 0, 'case_index': 0, 'status': 'passed', 'engine': 'manual',
              'title': 'Recovery', 'started_at': '2026-01-01T00:00:00Z',
              'finished_at': '2026-01-01T00:00:01Z'}
    if step_index is not None:
        result['step_index'] = step_index
    return result


def _evidence_count(factory):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(db.EvidenceRow))


def _assert_evidence(api, result):
    response = api.get(f"/api/v1/ai/evidence/{result['evidence_id']}")
    assert response.status_code == 200
    assert response.content == PNG


def test_periodic_expiry_after_start_preserves_partial_without_execution(recovery_run, monkeypatch):
    api, factory, claim = recovery_run
    clock = [int(time.time())]
    monkeypatch.setattr(worker, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(worker, '_POLL_SECONDS', 0.01)
    executions = []
    monkeypatch.setattr(worker, 'execute_claim', lambda *args, **kwargs: executions.append(args))
    assert worker._checkpoint_progress(factory, claim, _result(), [PNG])
    worker.start(factory)
    status = api.get(f"/api/v1/ai/suites/{claim['suite_id']}/status").json()
    assert status['status'] == 'running'
    clock[0] += int(worker._LEASE) + 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = _payload(api, claim)
        if state['status'] == 'interrupted':
            break
        time.sleep(0.01)
    else:
        pytest.fail('periodic reaper did not interrupt the expired attempt')
    worker.stop()
    assert executions == []
    assert len(state['results']) == 1
    _assert_evidence(api, state['results'][0])
    assert worker.claim(factory) is None


def test_process_crash_retains_completed_step_case_and_idempotent_evidence(recovery_run):
    api, factory, claim = recovery_run

    class ProcessCrash(BaseException):
        pass

    def execute(suite, profile, *, should_cancel, on_progress):
        for result in (_result(1), _result()):
            assert on_progress(result, [PNG, PNG])
            assert on_progress(dict(result), [PNG])
            persisted = _payload(api, claim)
            collection = 'step_results' if 'step_index' in result else 'results'
            assert len(persisted[collection]) == 1
            _assert_evidence(api, persisted[collection][0])
        raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        worker.execute_claim(factory, claim, executor=execute)
    assert _evidence_count(factory) == 2
    with factory() as session:
        row = session.get(db.AISuiteRow, claim['suite_id'])
        row.lease_expires_at = '0'
        session.commit()
    assert worker.recover_interrupted(factory) == 1
    state = _payload(api, claim)
    assert state['status'] == 'interrupted'
    assert len(state['results']) == len(state['step_results']) == 1
    assert state['counts']['passed'] == 1
    for result in state['results'] + state['step_results']:
        _assert_evidence(api, result)
    assert worker.claim(factory) is None


@pytest.mark.parametrize('cancel', [False, True])
def test_finalize_retains_steps_and_cases_without_duplicate_evidence(recovery_run, cancel):
    api, factory, claim = recovery_run

    def execute(suite, profile, *, should_cancel, on_progress):
        step, case = _result(1), _result()
        assert on_progress(step, [PNG])
        assert on_progress(case, [PNG])
        if cancel:
            response = api.post(f"/api/v1/ai/suites/{claim['suite_id']}/cancel")
            assert response.status_code == 200
            assert should_cancel()
            return [], []
        return [case], [(0, PNG), (0, PNG)]

    worker.execute_claim(factory, claim, executor=execute)
    state = _payload(api, claim)
    assert state['status'] == ('cancelled' if cancel else 'completed')
    assert len(state['results']) == len(state['step_results']) == 1
    assert state['counts'] == {'passed': 1, 'failed': 0, 'error': 0, 'skipped': 0}
    assert _evidence_count(factory) == 2
    for result in state['results'] + state['step_results']:
        _assert_evidence(api, result)


@pytest.mark.parametrize('changed_fence', ['attempt_id', 'attempt_version'])
def test_old_attempt_cannot_checkpoint_finalize_or_execute_new_fence(recovery_run, changed_fence):
    api, factory, claim = recovery_run

    def execute(suite, profile, *, should_cancel, on_progress):
        assert on_progress(_result(1), [PNG])
        with factory() as session:
            row = session.get(db.AISuiteRow, claim['suite_id'])
            if changed_fence == 'attempt_id':
                row.attempt_id = db.uid()
            else:
                row.attempt_version += 1
            row.payload = {**row.payload, 'message': 'new attempt fence'}
            session.commit()
        assert not on_progress(_result(), [PNG])
        assert should_cancel()
        return [_result()], [(0, PNG)]

    worker.execute_claim(factory, claim, executor=execute)
    executions = []
    worker.execute_claim(factory, claim, executor=lambda *args, **kwargs: executions.append(True))
    assert executions == []
    state = _payload(api, claim)
    assert state['message'] == 'new attempt fence'
    assert not state.get('results')
    assert len(state['step_results']) == 1
    assert _evidence_count(factory) == 1
    with factory() as session:
        assert session.get(db.AISuiteRow, claim['suite_id']).status == 'running'


def test_reaper_does_not_overwrite_locked_heartbeat_renewal(recovery_run):
    api, factory, claim = recovery_run
    with factory() as session:
        row = session.get(db.AISuiteRow, claim['suite_id'])
        row.lease_expires_at = '0'
        session.commit()
    with factory() as heartbeat:
        row = heartbeat.scalar(select(db.AISuiteRow).where(
            db.AISuiteRow.id == claim['suite_id'],
        ).with_for_update())
        row.lease_expires_at = str(int(time.time()) + int(worker._LEASE))
        heartbeat.flush()
        assert worker._reap_expired(factory) == 0
        heartbeat.commit()
    assert worker._reap_expired(factory) == 0
    status = api.get(f"/api/v1/ai/suites/{claim['suite_id']}/status").json()
    assert status['status'] == 'running'

def test_heartbeat_retries_transient_db_failures_and_keeps_lease(recovery_run, monkeypatch):
    """Один сбой БД не должен навсегда прекращать продление lease."""
    api, factory, claim = recovery_run
    monkeypatch.setattr(worker, '_HEARTBEAT_INTERVAL_SECONDS', 0.02)
    monkeypatch.setattr(worker, '_HEARTBEAT_BACKOFF_SECONDS', (0.02, 0.02))
    failures = {'left': 3}

    class FlakySession:
        def __init__(self, session):
            self._session = session

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._session.close()
            return False

        def scalar(self, *args, **kwargs):
            if failures['left']:
                failures['left'] -= 1
                raise RuntimeError('база временно недоступна')
            return self._session.scalar(*args, **kwargs)

        def commit(self):
            return self._session.commit()

    def flaky_factory():
        return FlakySession(factory())

    stop = threading.Event()
    keeper = threading.Thread(target=worker.heartbeat, args=(flaky_factory, claim, stop),
                              name='test-heartbeat', daemon=True)
    keeper.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with factory() as session:
            row = session.get(db.AISuiteRow, claim['suite_id'])
            if (row.payload or {}).get('heartbeat_errors'):
                break
        time.sleep(0.02)
    else:
        stop.set()
        pytest.fail('heartbeat не восстановился после сбоев БД')
    stop.set()
    keeper.join(timeout=5)

    assert not keeper.is_alive(), 'после восстановления heartbeat не должен завершаться'
    with factory() as session:
        row = session.get(db.AISuiteRow, claim['suite_id'])
        assert row.last_heartbeat_at
        assert int(row.lease_expires_at) > int(time.time())
        assert row.payload['heartbeat_errors'] == 3
        assert row.payload['heartbeat_lost']


def test_heartbeat_stops_when_attempt_fence_disappears(recovery_run, monkeypatch):
    """Исчезнувшая строка попытки (чужой fence) — единственный повод завершить поток."""
    api, factory, claim = recovery_run
    monkeypatch.setattr(worker, '_HEARTBEAT_INTERVAL_SECONDS', 0.02)
    monkeypatch.setattr(worker, '_HEARTBEAT_BACKOFF_SECONDS', (0.02,))
    with factory() as session:
        row = session.get(db.AISuiteRow, claim['suite_id'])
        row.attempt_version = int(row.attempt_version) + 1
        session.commit()

    stop = threading.Event()
    keeper = threading.Thread(target=worker.heartbeat, args=(factory, claim, stop),
                              name='test-heartbeat-fence', daemon=True)
    keeper.start()
    keeper.join(timeout=5)
    stop.set()

    assert not keeper.is_alive(), 'heartbeat должен выйти по потерянному fence'
