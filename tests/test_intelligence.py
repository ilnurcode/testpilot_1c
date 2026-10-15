from copy import deepcopy
from testpilot.demo import configurations, catalog, histories
from testpilot.impact.algorithms import analyze
from testpilot.contracts import ImpactPackage, checksum

def package():
    a,b=configurations()
    return analyze(a,b,{'edge_types':['calls','uses','belongs_to','subscribes_to'],'direction':'impact','max_depth':10})[1]

def load(client):
    p=package();r=client.post('/api/v1/impact-packages',json=p);assert r.status_code==200,r.text
    for t,cover,state in catalog():
        assert client.post('/api/v1/tests',json=t).status_code==200
        for oid in cover:
            assert client.put('/api/v1/tests/'+t['test_id']+'/coverage',json={'object_id':oid,'state':state,'reason':'Учебный эталон'}).status_code==200
    return p

def test_package_idempotency_validation(clients):
    _,b=clients;p=package()
    assert b.post('/api/v1/impact-packages',json=p).status_code==200
    assert b.post('/api/v1/impact-packages',json=p).json()['idempotent']
    p['warnings'].append('new');p['checksum']=checksum({k:v for k,v in p.items() if k!='checksum'})
    assert b.post('/api/v1/impact-packages',json=p).status_code==409
    p['checksum']='0'*64;assert b.post('/api/v1/impact-packages',json=p).status_code==422

def test_crud_permissions_audit(clients):
    _,b=clients;t=catalog()[0][0]
    assert b.post('/api/v1/tests',json={**t,'mandatory':False}).json()['mandatory']
    t['mandatory']=False
    assert b.put('/api/v1/tests/'+t['test_id'],json=t,auth=('analyst','test-password-only')).status_code==403
    assert b.put('/api/v1/tests/'+t['test_id'],json=t).status_code==403
    assert b.put('/api/v1/tests/'+t['test_id']+'?reason=Экспертное решение',json=t).status_code==200
    assert b.put('/api/v1/tests/'+t['test_id']+'/coverage',json={'object_id':'order','state':'confirmed','reason':'Проверено'},auth=('analyst','test-password-only')).status_code==403
    assert b.delete('/api/v1/tests/'+t['test_id']).status_code==200
    assert not b.get('/api/v1/tests/'+t['test_id']).json()['active']
    logs=b.get('/api/v1/audit').json();assert any(x['action']=='update_test' for x in logs)

def test_plan_history_exports_approval_job(clients):
    _,b=clients;p=load(b)
    for h in histories():
        r=b.post('/api/v1/history',json=h);assert r.status_code==200,r.text
    assert b.post('/api/v1/history',json=histories()[0]).json()['idempotent']
    r=b.post('/api/v1/plans',json={'package_id':p['package_id'],'budget_seconds':330,'min_coverage':.9,'use_ml':True})
    assert r.status_code==200,r.text
    plan=r.json();pid=plan['plan_id']
    assert plan['status']=='feasible' and plan['coverage_available']>=.9 and plan['duration_seconds']<=330
    assert 'demo-01' in plan['selected_ids'] and 'demo-14' not in plan['selected_ids'] and 'demo-15' not in plan['selected_ids']
    assert all('discount' not in t['covered_objects'] for t in plan['ranked_tests'] if t['test_id']=='demo-16')
    assert 'Нет обученной' in plan['ml_status']
    assert b.get(f'/api/v1/plans/{pid}').json()['selected_ids']==plan['selected_ids']
    assert b.get(f'/api/v1/plans/{pid}/export?format=json').json()['plan_id']==pid
    csv=b.get(f'/api/v1/plans/{pid}/export?format=csv');assert 'demo-01' in csv.text and 'text/csv' in csv.headers['content-type']
    assert b.post(f'/api/v1/plans/{pid}/execution-jobs').status_code==409
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Проверено'},auth=('analyst','test-password-only')).status_code==403
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Проверено'}).status_code==200
    job=b.post(f'/api/v1/plans/{pid}/execution-jobs').json()
    assert job['status']=='awaiting_external_runner' and not job['executor_connected']
    assert set(t['test_id'] for t in job['tests'])==set(plan['selected_ids'])
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Повтор'}).status_code==409

def test_conflict_and_manual_deviation(clients):
    _,b=clients;p=load(b)
    plan=b.post('/api/v1/plans',json={'package_id':p['package_id'],'budget_seconds':0,'min_coverage':1}).json();pid=plan['plan_id']
    assert plan['status']=='conflict' and plan['selected_ids']==['demo-01']
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Проверено'}).status_code==409
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Явное исключение бюджета','allow_deviation':True}).status_code==200
    assert b.get(f'/api/v1/plans/{pid}').json()['status']=='conflict'

def test_history_invalid_atomic(clients):
    _,b=clients;load(b);h=histories()[0];h['results'][-1]['test_id']='missing';h['defects']=[]
    assert b.post('/api/v1/history',json=h).status_code==404
    assert b.get('/api/v1/history').json()==[]

def test_ml_endpoint(clients):
    _,b=clients;load(b)
    r=b.post('/api/v1/ml/train',json={'configuration_id':'demo-sales','synthetic':True})
    assert r.status_code==200 and not r.json()['accepted']
    for h in histories():assert b.post('/api/v1/history',json=h).status_code==200
    r=b.post('/api/v1/ml/train',json={'configuration_id':'demo-sales','synthetic':True});assert r.status_code==200
    assert 'auc' in r.json() and 'coefficients' in r.json()

def test_execution_report_idempotent_and_immutable(clients):
    _,b=clients;p=load(b)
    plan=b.post('/api/v1/plans',json={'package_id':p['package_id'],'budget_seconds':330,'min_coverage':.9}).json();pid=plan['plan_id']
    assert b.post(f'/api/v1/plans/{pid}/approve',json={'reason':'Учебная проверка'}).status_code==200
    job=b.post(f'/api/v1/plans/{pid}/execution-jobs').json()
    from datetime import datetime,timedelta,timezone
    stamp=datetime(2026,2,1,tzinfo=timezone.utc)
    # Только явно синтетический входной отчёт; система сама не создаёт passed.
    run={'run_id':'external-synthetic-report','configuration_id':'demo-sales','version':'2.0','synthetic':True,
         'results':[{'test_id':t['test_id'],'status':'error','duration_seconds':1,'started_at':stamp.isoformat(),'finished_at':(stamp+timedelta(seconds=1)).isoformat(),'error':'Учебная ошибка внешнего исполнителя','artifacts':[]} for t in job['tests']], 'defects':[]}
    report={'execution_job_id':job['execution_job_id'],'run':run}
    assert b.post('/api/v1/execution-results',json=report,auth=('analyst','test-password-only')).status_code==403
    response=b.post('/api/v1/execution-results',json=report,auth=('runner','test-password-only'));assert response.status_code==200,response.text
    assert b.post('/api/v1/execution-results',json=report).json()['idempotent']
    report['run']['results'][0]['status']='passed'
    assert b.post('/api/v1/execution-results',json=report).status_code==409
    stored=b.get('/api/v1/execution-jobs/'+job['execution_job_id']).json()
    assert stored['status']=='reported' and not stored['executor_connected']

def test_critical_downgrade_requires_expert(clients):
    _,b=clients;t=catalog()[0][0];assert b.post('/api/v1/tests',json=t).status_code==200
    t.update(criticality='low',mandatory=False)
    assert b.put('/api/v1/tests/'+t['test_id'],json=t,auth=('analyst','test-password-only')).status_code==403

def test_failed_database_commit_is_not_success(clients):
    a,_=clients
    from testpilot.db import session,VersionRow
    from sqlalchemy.exc import IntegrityError
    # Имитирует конфликт при завершении транзакции: клиент должен получить 409, а не 200.
    def failing_session():
        for s in session():
            yield s
            raise IntegrityError('test',{},Exception('forced constraint conflict'))
    a.app.dependency_overrides[session]=failing_session
    try:
        r=a.post('/api/v1/imports',json=configurations()[0]);assert r.status_code==409
    finally:a.app.dependency_overrides.clear()
    assert a.get('/api/v1/versions').json()==[]
