import time

from test_ai import package


def _demo_suite(clients):
    impact, c = clients
    p = package()
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'Async runs', 'description': ''}).json()
    created = c.post('/api/v1/ai/suites', json={'package_id': p['package_id'], 'project_id': project['id'], 'provider': 'demo'})
    assert created.status_code == 200, created.text
    return c, created.json()


def _manualize(suite):
    from testpilot.db import AISuiteRow, _factory
    from sqlalchemy.orm.attributes import flag_modified
    with _factory() as session:
        row = session.get(AISuiteRow, suite['suite_id'])
        proposal = row.payload['suggestion']['proposals'][0]
        proposal['engine'] = 'manual'
        proposal['cases'] = [{'assertions': [{
            'id': 'manual-1', 'scope': 'manual', 'check': 'manual', 'engine': 'manual',
            'operator': 'manual', 'requirement': 'Проверить вручную', 'source_ref': 'ТЗ',
        }]}]
        row.payload = {**row.payload, 'suggestion': {**row.payload['suggestion'], 'proposals': row.payload['suggestion']['proposals']}}
        flag_modified(row, 'payload')
        session.commit()


def _approved_manual(impact, c, suite):
    _manualize(suite)
    proposals = suite['suggestion']['proposals']
    index = 0
    approved = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/approve",
                      json={'reason': 'async manual', 'selected_proposals': [index]})
    assert approved.status_code == 200, approved.text
    return approved.json()


def _wait(c, suite_id, wanted=('completed', 'error', 'cancelled', 'interrupted'), timeout=8):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        response = c.get(f'/api/v1/ai/suites/{suite_id}/status')
        assert response.status_code == 200, response.text
        state = response.json()
        if state['status'] in wanted:
            return state
        time.sleep(0.05)
    raise AssertionError(f'run did not reach {wanted}: {state}')


def test_run_1c_returns_queued_and_polls_to_terminal(clients):
    impact, c = clients
    c, suite = _demo_suite(clients)
    _approved_manual(impact, c, suite)
    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c",
                      headers={'Idempotency-Key': 'async-one'})
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued['status'] in ('queued', 'running')
    state = _wait(c, suite['suite_id'])
    import json as _json
    assert state['status'] == 'completed', _json.dumps(state, ensure_ascii=False)
    assert state['attempt_id']
    assert state['attempt_version'] == 1
    assert state['counts']['skipped'] == 1


def test_idempotency_returns_the_same_attempt(clients):
    impact, c = clients
    c, suite = _demo_suite(clients)
    _approved_manual(impact, c, suite)
    first = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c",
                   headers={'Idempotency-Key': 'same-key'})
    assert first.status_code == 202
    second = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c",
                    headers={'Idempotency-Key': 'same-key'})
    assert second.status_code == 202
    assert second.json()['attempt_id'] == first.json()['attempt_id']
    _wait(c, suite['suite_id'])


def test_old_suite_without_business_assertion_requires_reapproval(clients):
    impact, c = clients
    c, suite = _demo_suite(clients)
    connection = impact.put(f"/api/v1/projects/{suite['project_id']}/connection", json={
        'odata_url': 'http://127.0.0.1:8088/odata/standard.odata',
    })
    assert connection.status_code == 200, connection.text
    proposals = suite['suggestion']['proposals']
    index = next(i for i, proposal in enumerate(proposals) if proposal['engine'] == 'odata')
    approved = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/approve",
                      json={'reason': 'legacy', 'selected_proposals': [index]})
    assert approved.status_code == 200
    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    assert response.status_code == 422
    assert 'business' in response.text or 'согласован' in response.text
    assert c.get(f"/api/v1/ai/suites/{suite['suite_id']}/status").json()['status'] == 'approved'


def test_cancel_stops_before_next_step_and_keeps_marker(clients, monkeypatch):
    import threading
    from testpilot.ai import executor

    impact, c = clients
    c, suite = _demo_suite(clients)
    _approved_manual(impact, c, suite)
    started = threading.Event()
    release = threading.Event()

    def fake_execute(suite, profile, should_cancel=None, on_progress=None):
        started.set()
        while not release.wait(0.01):
            if should_cancel and should_cancel():
                return ([{'proposal_index': 0, 'case_index': 0, 'engine': 'manual',
                          'title': 'manual', 'status': 'skipped', 'started_at': 'test'}], [])
        return ([], [])

    monkeypatch.setattr(executor, 'execute_1c', fake_execute)
    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    assert response.status_code == 202
    assert started.wait(5)
    cancelled = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/cancel")
    assert cancelled.status_code == 200
    release.set()
    state = _wait(c, suite['suite_id'])
    assert state['status'] == 'cancelled'
    assert state['cancel_requested'] is True


def test_rerun_returns_202_without_overwriting_source_or_copying_step_progress(clients):
    from testpilot import db
    impact, c = clients
    c, suite = _demo_suite(clients)
    _approved_manual(impact, c, suite)
    from test_ai import _demote_demo_plan
    _demote_demo_plan(suite['suite_id'])  # rerun учебного demo-плана отклоняется; проверяем механику копии
    sid = suite['suite_id']
    response = c.post(f'/api/v1/ai/suites/{sid}/run-1c')
    assert response.status_code == 202, response.text
    assert _wait(c, sid)['status'] == 'completed'
    factory = db._factory
    with factory() as session:
        row = session.get(db.AISuiteRow, sid)
        row.payload = {**row.payload, 'step_results': [{'proposal_index': 0, 'case_index': 0,
                                                      'step_index': 1, 'status': 'passed'}]}
        session.commit()
    original = c.get(f'/api/v1/ai/suites/{sid}').json()
    response = c.post(f'/api/v1/ai/suites/{sid}/rerun')
    assert response.status_code == 202, response.text
    new_id = response.json()['suite_id']
    assert new_id != sid
    assert _wait(c, new_id)['status'] == 'completed'
    rerun = c.get(f'/api/v1/ai/suites/{new_id}').json()
    assert not rerun.get('step_results')
    assert c.get(f'/api/v1/ai/suites/{sid}').json() == original
