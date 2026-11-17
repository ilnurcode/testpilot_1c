"""Адресное удаление прогона (suite) с каскадом. Требует PostgreSQL (фикстура clients)."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from testpilot import db
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def _package():
    return analyze(sample(1), sample(2), {'edge_types': ['calls', 'uses', 'belongs_to', 'subscribes_to'],
                                          'direction': 'impact', 'max_depth': 10})[1]


def _session():
    return sessionmaker(db.engine(), expire_on_commit=False)()


def _project_and_suites(clients, count: int = 2):
    impact, c = clients
    project = impact.post('/api/v1/projects', json={'name': 'Удаление прогона', 'description': ''}).json()
    pkg = _package()
    assert c.post('/api/v1/impact-packages', json=pkg).status_code == 200
    suites = []
    for _ in range(count):
        created = c.post('/api/v1/ai/suites', json={
            'package_id': pkg['package_id'], 'project_id': project['id'], 'provider': 'local',
        })
        assert created.status_code == 200, created.text
        suites.append(created.json()['suite_id'])
    return project, pkg['package_id'], suites


def _seed(suite_id: str, package_id: str) -> dict:
    """Связанные строки: evidence, тест ai-<sid>-1 с coverage/priority, прогон ai-run-<sid> с result/defect."""
    plan_id = str(uuid4())
    evidence_id = str(uuid4())
    test_id = f'ai-{suite_id}-1'
    run_id = f'ai-run-{suite_id}'
    with _session() as s:
        s.add(db.TestRow(id=test_id, configuration_id='cfg', active=True, mandatory=False,
                         criticality='medium', duration_seconds=0.1, payload={}))
        s.flush()
        s.add(db.CoverageRow(test_id=test_id, object_id='document.x', state='covered',
                             source='test', actor='expert', reason='unit'))
        s.add(db.PlanRow(id=plan_id, package_id=package_id, payload={}, approved=True))
        s.flush()
        s.add(db.PriorityRow(plan_id=plan_id, test_id=test_id, score=1.0, factors={}))
        s.add(db.RunRow(id=run_id, configuration_id='cfg', version='1.0', synthetic=False,
                        checksum='0' * 64))
        s.flush()
        s.add(db.ResultRow(run_id=run_id, test_id=test_id, status='passed', duration_seconds=0.1,
                           started_at='unit', finished_at='unit', payload={}))
        s.add(db.DefectRow(id=f'def-{suite_id}', run_id=run_id, test_id=test_id,
                           severity='low', state='open', description='unit'))
        s.add(db.EvidenceRow(id=evidence_id, suite_id=suite_id, content=b'\x89PNG' + b'x' * 200))
        s.commit()
    return {'test_id': test_id, 'run_id': run_id, 'plan_id': plan_id, 'evidence_id': evidence_id}


def _counts(suite_id: str, seed: dict) -> dict:
    with _session() as s:
        return {
            'suite': s.scalar(select(db.AISuiteRow.id).where(db.AISuiteRow.id == suite_id)),
            'evidence': s.scalar(select(db.EvidenceRow.id).where(db.EvidenceRow.id == seed['evidence_id'])),
            'test': s.scalar(select(db.TestRow.id).where(db.TestRow.id == seed['test_id'])),
            'coverage': s.scalar(select(db.CoverageRow.test_id).where(db.CoverageRow.test_id == seed['test_id'])),
            'priority': s.scalar(select(db.PriorityRow.test_id).where(db.PriorityRow.test_id == seed['test_id'])),
            'run': s.scalar(select(db.RunRow.id).where(db.RunRow.id == seed['run_id'])),
            'result': s.scalar(select(db.ResultRow.id).where(db.ResultRow.run_id == seed['run_id'])),
            'defect': s.scalar(select(db.DefectRow.id).where(db.DefectRow.run_id == seed['run_id'])),
        }


def test_delete_suite_requires_confirm(clients):
    _, c = clients
    _, _, (suite_id, _) = _project_and_suites(clients)

    response = c.delete(f'/api/v1/ai/suites/{suite_id}')

    assert response.status_code == 409, response.text
    assert f'подтвердите удаление: передайте confirm={suite_id}' in response.json()['error']['message']


def test_delete_suite_with_wrong_confirm_is_rejected(clients):
    _, c = clients
    _, _, (suite_id, _) = _project_and_suites(clients)

    response = c.delete(f'/api/v1/ai/suites/{suite_id}', params={'confirm': 'not-the-id'})

    assert response.status_code == 409, response.text
    assert suite_id in response.json()['error']['message']


def test_delete_unknown_suite_is_404(clients):
    _, c = clients
    missing = str(uuid4())

    response = c.delete(f'/api/v1/ai/suites/{missing}', params={'confirm': missing})

    assert response.status_code == 404, response.text


def test_delete_suite_removes_cascade_rows(clients):
    _, c = clients
    project, package_id, (suite_id, neighbor) = _project_and_suites(clients)
    seed = _seed(suite_id, package_id)
    neighbor_seed = _seed(neighbor, package_id)

    response = c.delete(f'/api/v1/ai/suites/{suite_id}', params={'confirm': suite_id})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['suite_id'] == suite_id
    assert body['deleted']['evidence'] == 1
    assert body['deleted']['tests'] == 1
    assert body['deleted']['runs'] == 1
    assert body['deleted']['suites'] == 1
    assert _counts(suite_id, seed) == {
        'suite': None, 'evidence': None, 'test': None, 'coverage': None,
        'priority': None, 'run': None, 'result': None, 'defect': None,
    }


def test_delete_suite_keeps_neighbor_and_package(clients):
    _, c = clients
    project, package_id, (suite_id, neighbor) = _project_and_suites(clients)
    seed = _seed(suite_id, package_id)
    neighbor_seed = _seed(neighbor, package_id)

    assert c.delete(f'/api/v1/ai/suites/{suite_id}', params={'confirm': suite_id}).status_code == 200

    intact = _counts(neighbor, neighbor_seed)
    assert intact['suite'] == neighbor
    assert intact['evidence'] == neighbor_seed['evidence_id']
    assert intact['test'] == neighbor_seed['test_id']
    assert intact['run'] == neighbor_seed['run_id']
    with _session() as s:
        assert s.get(db.PackageRow, package_id) is not None, 'пакет должен остаться'
        assert s.get(db.PlanRow, seed['plan_id']) is not None, 'план должен остаться'


def test_delete_suite_keeps_package_and_plan(clients):
    _, c = clients
    project, package_id, (suite_id, _neighbor) = _project_and_suites(clients)
    seed = _seed(suite_id, package_id)

    response = c.delete(f'/api/v1/ai/suites/{suite_id}', params={'confirm': suite_id})

    assert response.status_code == 200, response.text
    with _session() as s:
        assert s.get(db.PackageRow, package_id) is not None
        assert s.get(db.PlanRow, seed['plan_id']) is not None
        assert s.get(db.AISuiteRow, suite_id) is None
