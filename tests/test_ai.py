import csv
import io
import json
import httpx
import pytest
from fastapi import HTTPException
from testpilot.ai import provider,codex
from testpilot.ai.models import Suggestion
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def package():return analyze(sample(1),sample(2),{'edge_types':['calls','uses','belongs_to','subscribes_to'],'direction':'impact','max_depth':10})[1]

def mocked_model_suggestion(p):
    suggestion = provider.demo_suggestion(p)
    for proposal in suggestion.proposals:
        for case in proposal.cases:
            if proposal.engine == 'odata':
                case.entity = 'Document_ЗаказКлиента'
            elif proposal.engine == 'playwright':
                case.steps = [{'action': 'assert_text', 'target': 'Продажи'}]
    return suggestion

def test_api_provider_structured_response_and_secret_not_returned(monkeypatch):
    monkeypatch.setenv('TP_LLM_API_KEY','unit-private-key');monkeypatch.setenv('TP_LLM_MODEL','unit-model')
    p=package();expected=provider.demo_suggestion(p)
    seen=[]
    def respond(r):
        seen.append(r);return httpx.Response(200,json={'choices':[{'message':{'content':expected.model_dump_json()}}]})
    result,model=provider.generate(p,[],{},transport=httpx.MockTransport(respond))
    assert result.proposals and model=='unit-model'
    assert seen[0].headers['authorization']=='Bearer unit-private-key'
    assert 'unit-private-key' not in result.model_dump_json()
    assert 'unit-private-key' not in json.dumps(provider.status())

def test_provider_retries_schema_validation_once(monkeypatch):
    monkeypatch.setenv('TP_LLM_API_KEY','unit');monkeypatch.setenv('TP_LLM_MODEL','unit-model')
    p=package();expected=provider.demo_suggestion(p);seen=[]
    replies=[
        {'choices':[{'message':{'content':'{"summary":"invalid","proposals":[]}'}}]},
        {'choices':[{'message':{'content':expected.model_dump_json()}}]},
    ]
    def respond(r):
        seen.append(r)
        return httpx.Response(200,json=replies[len(seen)-1])
    result,model=provider.generate(p,[],{},transport=httpx.MockTransport(respond))
    assert len(seen)==2 and result.proposals and model=='unit-model'


@pytest.mark.parametrize('reply',[{'choices':[]},{'choices':[{'message':{'content':'not json'}}]},{'choices':[{'message':{'content':'{"summary":"x","proposals":[]}'}}]}])
def test_invalid_model_response_is_rejected(monkeypatch,reply):
    monkeypatch.setenv('TP_LLM_API_KEY','unit')
    with pytest.raises(HTTPException) as e:provider.generate(package(),[],{},transport=httpx.MockTransport(lambda r:httpx.Response(200,json=reply)))
    assert e.value.status_code==502

@pytest.mark.parametrize('status,reason',[(402,'недостаточно средств'),(429,'Исчерпан лимит'),(401,'ключ нейросети')])
def test_provider_quota_errors_are_explained_without_upstream_body(monkeypatch,status,reason):
    monkeypatch.setenv('TP_LLM_API_KEY','unit');monkeypatch.setenv('TP_LLM_MODEL','opencode/glm-5.3')
    transport=httpx.MockTransport(lambda r:httpx.Response(status,text='{"error":{"message":"secret upstream detail"}}'))
    with pytest.raises(HTTPException) as e:provider.generate(package(),[],{},transport=transport)
    assert e.value.status_code==502
    message=e.value.detail['message']
    assert reason in message and e.value.detail['upstream_status']==status
    assert 'secret upstream detail' not in json.dumps(e.value.detail)

def test_missing_key_does_not_fall_back_secretly(monkeypatch):
    monkeypatch.delenv('TP_LLM_API_KEY',raising=False)
    with pytest.raises(HTTPException) as e:provider.generate(package(),[],{})
    assert e.value.status_code==503

def test_template_only_for_isolated_synthetic_project():
    p=package();p['synthetic']=False
    with pytest.raises(HTTPException):provider.demo_suggestion(p)

def test_generation_approval_persistence_and_guards(clients):
    impact,c=clients;p=package();assert c.post('/api/v1/impact-packages',json=p).status_code==200
    project=impact.post('/api/v1/projects',json={'name':'Проект прогонов','description':''}).json()
    r=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':project['id'],'provider':'demo'})
    assert r.status_code==200,r.text
    s=r.json();sid=s['suite_id'];assert s['provider']=='demo' and len(s['suggestion']['proposals'])==2
    assert c.post(f'/api/v1/ai/suites/{sid}/run-demo').status_code==409
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Test','selected_proposals':[99]}).status_code==422
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Test','selected_proposals':[0]},auth=('analyst','test-password-only')).status_code==403
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Проверено','selected_proposals':[0]}).status_code==200
    assert c.get(f'/api/v1/ai/suites/{sid}').json()['status']=='approved'
    assert len(c.get(f"/api/v1/ai/suites?project_id={project['id']}").json())==1

@pytest.mark.parametrize('name',['llm','codex'])
def test_consent_required_before_external_request(clients,monkeypatch,name):
    _,c=clients;p=package();c.post('/api/v1/impact-packages',json=p)
    module=codex if name=='codex' else provider
    monkeypatch.setattr(module,'generate',lambda *a,**k:pytest.fail('Контекст передан без согласия'))
    r=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':'unit','provider':name,'consent':False})
    assert r.status_code==422

@pytest.mark.parametrize('name',['llm','codex'])
def test_unknown_objects_and_tests_rejected(clients,monkeypatch,name):
    _,c=clients;p=package();c.post('/api/v1/impact-packages',json=p)
    catalog_test={'test_id':'known-test','title':'Known','configuration_id':p['configuration_id'],'duration_seconds':10,'synthetic':p['synthetic']}
    assert c.post('/api/v1/tests',json=catalog_test).status_code==200
    suggestion=mocked_model_suggestion(p);suggestion.proposals[0].object_ids=['missing']
    module=codex if name=='codex' else provider
    monkeypatch.setattr(module,'generate',lambda *a,**k:(suggestion,'test'))
    body={'package_id':p['package_id'],'project_id':'unit','provider':name,'consent':True}
    assert c.post('/api/v1/ai/suites',json=body).status_code==502
    suggestion.proposals[0].object_ids=p['affected_objects'][:1];suggestion.selected_test_ids=['missing']
    assert c.post('/api/v1/ai/suites',json=body).status_code==502


def test_llm_suite_with_objects_and_no_catalog_keeps_valid_proposals(clients,monkeypatch):
    from testpilot.contracts import checksum
    _,c=clients;p=package();p['configuration_id']='proj-unmapped';p['synthetic']=False
    p['checksum']=checksum({k:v for k,v in p.items() if k!='checksum'})
    assert c.post('/api/v1/impact-packages',json=p).status_code==200
    proposal={'title':'Безопасная навигация','rationale':'Проверка доступности интерфейса','engine':'playwright',
              'object_ids':[p['affected_objects'][0]],'steps':['Открыть раздел без изменения данных'],
              'expected_result':'Раздел открылся','cases':[{'steps':[{'action':'assert_text','target':'Продажи'}],'expected':'Раздел открыт'}]}
    suggestion=Suggestion.model_validate({'summary':'План для разобранной модели','selected_test_ids':['invented-test'],
                                          'proposals':[proposal],'warnings':[]})
    seen={}
    def generate(_package,catalog,*args):
        seen['catalog']=catalog
        return suggestion,'mock-model'
    monkeypatch.setattr(provider,'generate',generate)
    response=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':'unit-project','provider':'llm','consent':True})
    assert response.status_code==200,response.text
    suite=response.json()
    assert seen['catalog']==[]
    assert suite['suggestion']['selected_test_ids']==[]
    assert any('Совместимый каталог тестов пуст' in warning for warning in suite['warnings'])
    system,_=provider.generation_messages(p,[],{})
    assert 'если каталог пуст, верни пустой список' in system

def test_real_projects_cannot_use_demo_executor(clients,monkeypatch):
    from testpilot.contracts import checksum
    _,c=clients;p=package();suggestion=mocked_model_suggestion(p);p['synthetic']=False
    p['checksum']=checksum({k:v for k,v in p.items() if k!='checksum'})
    c.post('/api/v1/impact-packages',json=p)
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:(suggestion,'unit-model'))
    r=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':'unit','provider':'llm','consent':True});sid=r.json()['suite_id']
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Unit','selected_proposals':[0]}).status_code==200
    assert c.post(f'/api/v1/ai/suites/{sid}/run-demo').status_code==409

def test_1c_web_run_rejects_authorization_without_web_credentials(clients,monkeypatch):
    from testpilot.contracts import checksum
    from testpilot.integration import webclient

    impact,intelligence=clients
    project=impact.post('/api/v1/projects',json={'name':'Web auth guard'}).json()
    pid=project['id']
    connection=impact.put(f'/api/v1/projects/{pid}/connection',json={
        'web_url':'http://localhost:8088/DemoTrd/',
        'odata_url':'http://localhost:8088/DemoTrd/odata/standard.odata',
        'auth_username':None,
        'auth_password':None,
    })
    assert connection.status_code==200

    p=package()
    suggestion=mocked_model_suggestion(p)
    p['synthetic']=False
    p['checksum']=checksum({k:v for k,v in p.items() if k!='checksum'})
    assert intelligence.post('/api/v1/impact-packages',json=p).status_code==200
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:(suggestion,'unit-model'))
    suite=intelligence.post('/api/v1/ai/suites',json={
        'package_id':p['package_id'],'project_id':pid,'provider':'llm','consent':True,
    }).json()
    sid=suite['suite_id']
    assert intelligence.post(f'/api/v1/ai/suites/{sid}/approve',json={
        'reason':'Проверка отсутствия веб-аутентификации','selected_proposals':[1],
    }).status_code==200

    monkeypatch.delenv('TP_1C_USERNAME',raising=False)
    monkeypatch.delenv('TP_1C_PASSWORD',raising=False)
    monkeypatch.setenv('TP_1C_AUTHORIZATION','Bearer test-authorization')
    monkeypatch.setattr(webclient,'WebClientRunner',lambda *a,**kw:pytest.fail('Браузер не должен запускаться'))
    response=intelligence.post(f'/api/v1/ai/suites/{sid}/run-1c')
    assert response.status_code==422
    assert response.json()['error']['message']=='Для сценариев веб-клиента задайте пользователя и пароль 1С в настройках подключения проекта; Authorization используется только для OData'


def test_llm_settings_api(clients, monkeypatch, tmp_path):
    _, c = clients
    # Настройки персистятся в .env рабочего каталога: уводим запись в tmp, чтобы не портить реальный .env.
    monkeypatch.chdir(tmp_path)
    # 1. Чтение настроек
    st = c.get('/api/v1/ai/settings/llm').json()
    assert 'base_url' in st and 'model' in st and 'has_key' in st

    # 2. Обновление настроек с вводом нового ключа
    payload = {
        'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4o-mini',
        'api_key': 'sk-test-secret-key-123'
    }
    r = c.put('/api/v1/ai/settings/llm', json=payload)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res['model'] == 'gpt-4o-mini'
    assert res['has_key'] is True
    # Убеждаемся, что секретный ключ не отдаётся наружу
    assert 'sk-test-secret-key-123' not in json.dumps(res)


def _suite_plan(p, engine, cases):
    return Suggestion.model_validate({
        'summary': 'Структурно проверяемый план',
        'selected_test_ids': [],
        'proposals': [{
            'title': 'Проверка',
            'rationale': 'Мок-план',
            'engine': engine,
            'object_ids': [p['affected_objects'][0]],
            'steps': ['Выполнить безопасную проверку'],
            'expected_result': 'Проверка завершена',
            'cases': cases,
        }],
        'warnings': [],
    })


def _create_mocked_llm_suite(clients, monkeypatch, p, suggestion):
    impact, c = clients
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'Проект мок-плана', 'description': ''}).json()
    monkeypatch.setattr(provider, 'generate', lambda *a, **kw: (suggestion, 'mock-model'))
    response = c.post('/api/v1/ai/suites', json={
        'package_id': p['package_id'],
        'project_id': project['id'],
        'provider': 'llm',
        'consent': True,
    })
    return c, response, project['id']


def test_suite_rejects_unknown_playwright_action_before_persistence(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'playwright', [{'steps': [{'action': 'hover', 'target': 'Продажи'}]}])
    _, response, _project = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'hover' in response.text


def test_structurally_valid_mocked_plan_creates_suite(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'playwright', [{'steps': [{'action': 'assert_text', 'target': 'Продажи'}]}])
    _, response, _project = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'draft'


def test_suite_rejects_odata_case_without_entity_or_metadata_check(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'odata', [{'expected': 'HTTP 200 OK'}])
    _, response, _project = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'entity' in response.text


def test_suite_accepts_manual_proposal_without_cases(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'manual', [])
    _, response, _project = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'draft'


def test_suite_rejects_unknown_engine_with_proposal_index(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'unsupported-engine', [{'entity': 'Document_Заказ'}])
    _, response, _project = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'unsupported-engine' in response.text


def test_suite_rejects_generated_open_url_before_persistence(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(
        p,
        'playwright',
        [{'steps': [{'action': 'open_url', 'target': 'https://evil.example/'}]}],
    )
    c, response, project_id = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert (
        "Предложение 0, case 0, шаг 0: действие open_url не допускается в сгенерированном плане; "
        "вход в веб-клиент выполняет исполнитель"
    ) in response.text
    assert c.get(f'/api/v1/ai/suites?project_id={project_id}').json() == []


SUITE_HISTORY_FIELDS = {
    'suite_id',
    'package_id',
    'model',
    'provider',
    'status',
    'counts',
    'created_at',
    'finished_at',
    'approved_count',
    'mutating_count',
}


def _project_with_package(clients):
    impact, c = clients
    p = package()
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'История прогонов', 'description': ''}).json()
    return impact, c, project, p


def _create_demo_suite(c, p, project_id):
    response = c.post('/api/v1/ai/suites', json={'package_id': p['package_id'], 'project_id': project_id, 'provider': 'demo'})
    assert response.status_code == 200, response.text
    return response.json()


def test_suite_history_is_empty_for_new_project(clients):
    impact, c, project, p = _project_with_package(clients)
    response = c.get('/api/v1/ai/suites', params={'project_id': project['id']})
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_suite_history_lists_runs_newest_first(clients):
    import time

    impact, c, project, p = _project_with_package(clients)
    first = _create_demo_suite(c, p, project['id'])
    time.sleep(0.01)
    second = _create_demo_suite(c, p, project['id'])

    rows = c.get('/api/v1/ai/suites', params={'project_id': project['id']}).json()
    assert [row['suite_id'] for row in rows] == [second['suite_id'], first['suite_id']]
    assert [row['created_at'] for row in rows] == sorted((row['created_at'] for row in rows), reverse=True)


def test_suite_history_rejects_bad_limits(clients):
    impact, c, project, p = _project_with_package(clients)
    assert c.get('/api/v1/ai/suites', params={'project_id': project['id'], 'limit': 0}).status_code == 400
    assert c.get('/api/v1/ai/suites', params={'project_id': project['id'], 'limit': 101}).status_code == 400
    assert c.get('/api/v1/ai/suites', params={'project_id': project['id'], 'limit': 1}).status_code == 200


def test_suite_history_unknown_project_is_404(clients):
    impact, c, project, p = _project_with_package(clients)
    missing = c.get('/api/v1/ai/suites', params={'project_id': '00000000-0000-0000-0000-000000000000'})
    assert missing.status_code == 404
    assert c.get('/api/v1/ai/suites', params={'project_id': project['id']}).status_code == 200


def test_suite_history_omits_heavy_fields_and_secrets(clients, monkeypatch):
    monkeypatch.setenv('TP_LLM_API_KEY', 'sk-history-secret')
    impact, c, project, p = _project_with_package(clients)
    created = _create_demo_suite(c, p, project['id'])
    assert created['suggestion']['proposals']

    response = c.get('/api/v1/ai/suites', params={'project_id': project['id']})
    rows = response.json()
    assert len(rows) == 1
    assert set(rows[0]) == SUITE_HISTORY_FIELDS
    assert set(rows[0]['counts']) == {'passed', 'failed', 'error', 'skipped'}
    assert rows[0]['counts'] == {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
    assert rows[0]['finished_at'] is None
    assert rows[0]['model'] is None and rows[0]['provider'] == 'demo'
    for forbidden in ('suggestion', 'payload', 'results', 'approval', 'warnings', 'affected_objects'):
        assert forbidden not in rows[0]
    assert 'sk-history-secret' not in response.text
    assert 'Проверить расчёт скидки' not in response.text
    assert 'Расчёт суммы через HTTP API учебного стенда' not in response.text


def test_suite_history_reports_approval_count(clients):
    impact, c, project, p = _project_with_package(clients)
    created = _create_demo_suite(c, p, project['id'])
    assert c.post(f"/api/v1/ai/suites/{created['suite_id']}/approve",
                  json={'reason': 'История', 'selected_proposals': [0, 1]}).status_code == 200

    row = c.get('/api/v1/ai/suites', params={'project_id': project['id']}).json()[0]
    assert row['status'] == 'approved'
    assert row['approved_count'] == 2


def _approved_suite_for_rerun(clients, monkeypatch, *, fail=None):
    impact, c, project, p = _project_with_package(clients)
    assert impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={'odata_url': 'https://1c.example.test/odata/standard.odata'},
    ).status_code == 200
    created = _create_demo_suite(c, p, project['id'])
    assert c.post(
        f"/api/v1/ai/suites/{created['suite_id']}/approve",
        json={'reason': 'Повторный прогон', 'selected_proposals': [0]},
    ).status_code == 200

    from testpilot.ai import executor as executor_module

    def fake_execute(suite, profile):
        if fail:
            raise RuntimeError(fail)
        return ([{
            'proposal_index': suite['approval']['selected_proposals'][0],
            'case_index': 0,
            'engine': 'odata',
            'title': 'Повтор',
            'status': 'passed',
            'message': 'Успешный OData-запрос',
            'started_at': 'unit',
        }], [])

    monkeypatch.setattr(executor_module, 'execute_1c', fake_execute)
    return impact, c, project, created


def test_rerun_creates_new_suite_with_copied_plan(clients, monkeypatch):
    impact, c, project, source = _approved_suite_for_rerun(clients, monkeypatch)
    before = c.get(f"/api/v1/ai/suites/{source['suite_id']}").json()

    response = c.post(f"/api/v1/ai/suites/{source['suite_id']}/rerun")
    assert response.status_code == 200, response.text
    new = response.json()

    assert new['suite_id'] != source['suite_id']
    assert new['suggestion'] == before['suggestion']
    assert new['approval'] == before['approval']
    for key in ('package_id', 'project_id', 'provider', 'model', 'target_version', 'affected_objects'):
        assert new[key] == before[key]
    assert new['status'] == 'completed'
    assert new['counts'] == {'passed': 1, 'failed': 0, 'error': 0, 'skipped': 0}
    assert new['results'][0]['status'] == 'passed'


def test_rerun_does_not_modify_source_suite(clients, monkeypatch):
    impact, c, project, source = _approved_suite_for_rerun(clients, monkeypatch)
    before = c.get(f"/api/v1/ai/suites/{source['suite_id']}").json()
    assert c.post(f"/api/v1/ai/suites/{source['suite_id']}/rerun").status_code == 200

    after = c.get(f"/api/v1/ai/suites/{source['suite_id']}").json()
    assert after == before
    assert after['status'] == 'approved'
    assert 'counts' not in after and after['results'] == []


def test_rerun_without_approved_proposals_is_409(clients):
    impact, c, project, p = _project_with_package(clients)
    draft = _create_demo_suite(c, p, project['id'])

    response = c.post(f"/api/v1/ai/suites/{draft['suite_id']}/rerun")
    assert response.status_code == 409
    assert 'нет утверждённых сценариев' in response.text
    assert len(c.get('/api/v1/ai/suites', params={'project_id': project['id']}).json()) == 1


def test_rerun_unknown_suite_is_404(clients):
    impact, c, project, p = _project_with_package(clients)
    assert c.post('/api/v1/ai/suites/00000000-0000-0000-0000-000000000000/rerun').status_code == 404


def test_rerun_appears_first_in_project_history(clients, monkeypatch):
    impact, c, project, source = _approved_suite_for_rerun(clients, monkeypatch)
    new = c.post(f"/api/v1/ai/suites/{source['suite_id']}/rerun").json()

    rows = c.get('/api/v1/ai/suites', params={'project_id': project['id']}).json()
    assert [row['suite_id'] for row in rows] == [new['suite_id'], source['suite_id']]
    assert rows[0]['status'] == 'completed' and rows[1]['status'] == 'approved'


def test_rerun_infrastructure_failure_marks_new_suite_error(clients, monkeypatch):
    impact, c, project, source = _approved_suite_for_rerun(clients, monkeypatch, fail='1С недоступна на стенде')
    before = c.get(f"/api/v1/ai/suites/{source['suite_id']}").json()

    response = c.post(f"/api/v1/ai/suites/{source['suite_id']}/rerun")
    assert response.status_code == 200, response.text
    failed = response.json()

    assert failed['suite_id'] != source['suite_id']
    assert failed['status'] == 'error'
    assert '1С недоступна на стенде' in failed['message']
    assert failed['counts'] == {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}

    stored = c.get(f"/api/v1/ai/suites/{failed['suite_id']}").json()
    assert stored['status'] == 'error' and stored['message'] == failed['message']
    assert c.get(f"/api/v1/ai/suites/{source['suite_id']}").json() == before


def _executed_suite(clients, monkeypatch):
    impact, c, project, p = _project_with_package(clients)
    assert impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={'odata_url': 'https://1c.example.test/odata/standard.odata'},
    ).status_code == 200
    created = _create_demo_suite(c, p, project['id'])
    assert c.post(
        f"/api/v1/ai/suites/{created['suite_id']}/approve",
        json={'reason': 'Отчёт о прогоне', 'selected_proposals': [0, 1]},
    ).status_code == 200

    from testpilot.ai import executor as executor_module

    def fake_execute(suite, profile):
        return ([
            {'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Проверка интерфейса',
             'status': 'passed', 'message': 'Раздел «Продажи» подтверждён', 'duration_seconds': 1.25,
             'started_at': 'unit', 'web_url': 'http://host.docker.internal:8088/DemoTrd'},
            {'proposal_index': 1, 'case_index': 0, 'engine': 'odata', 'title': 'Проверка OData',
             'status': 'failed', 'message': 'Сущность OData не найдена (HTTP 404)', 'duration_seconds': 0.5,
             'started_at': 'unit', 'actual': None},
        ], [(0, b'\x89PNG-unit')])

    monkeypatch.setattr(executor_module, 'execute_1c', fake_execute)
    run = c.post(f"/api/v1/ai/suites/{created['suite_id']}/run-1c")
    assert run.status_code == 200, run.text
    return c, project, run.json()


def test_run_report_markdown_contains_result_and_evidence(clients, monkeypatch):
    c, project, run = _executed_suite(clients, monkeypatch)
    response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report")

    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/markdown')
    assert f'run-{run["suite_id"]}.md' in response.headers['content-disposition']
    body = response.text
    assert body.startswith('# Отчёт о прогоне проверок')
    assert f'Проект: {project["name"]}' in body
    assert f'Пакет: {run["package_id"]}' in body
    assert '- Пройдено: 1' in body and '- Расхождений: 1' in body
    assert 'Раздел «Продажи» подтверждён' in body
    assert f"evidence {run['results'][0]['evidence_id']}" in body
    assert '### 1.' in body and '### 2.' in body
    assert 'Результатов нет' not in body


def test_run_report_csv_has_header_and_row_per_result(clients, monkeypatch):
    c, project, run = _executed_suite(clients, monkeypatch)
    response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report", params={'format': 'csv'})

    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    assert response.content.startswith(
        'Предложение;Case;Движок;Проверка;Статус;Сообщение;Длительность, мс;Evidence'.encode('utf-8'))
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert rows[0] == ['Предложение', 'Case', 'Движок', 'Проверка', 'Статус', 'Сообщение', 'Длительность, мс', 'Evidence']
    assert len(rows) == len(run['results']) + 1 == 3
    assert rows[1][1] == '1' and rows[1][4] == 'passed' and rows[1][7] == run['results'][0]['evidence_id']
    assert rows[1][6] == '1250'
    assert rows[2][4] == 'failed' and 'HTTP 404' in rows[2][5]


def test_run_report_rejects_unknown_format(clients, monkeypatch):
    c, project, run = _executed_suite(clients, monkeypatch)
    assert c.get(f"/api/v1/ai/suites/{run['suite_id']}/report", params={'format': 'xml'}).status_code == 400


def test_run_report_unknown_suite_is_404(clients):
    impact, c, project, p = _project_with_package(clients)
    response = c.get('/api/v1/ai/suites/00000000-0000-0000-0000-000000000000/report')
    assert response.status_code == 404


def test_run_report_without_run_marks_no_results(clients):
    impact, c, project, p = _project_with_package(clients)
    created = _create_demo_suite(c, p, project['id'])
    assert c.post(f"/api/v1/ai/suites/{created['suite_id']}/approve",
                  json={'reason': 'Без прогона', 'selected_proposals': [0]}).status_code == 200

    response = c.get(f"/api/v1/ai/suites/{created['suite_id']}/report")
    assert response.status_code == 200, response.text
    assert 'Результатов нет: прогон ещё не выполнялся.' in response.text
    assert '- Пройдено: 0' in response.text

    csv_response = c.get(f"/api/v1/ai/suites/{created['suite_id']}/report", params={'format': 'csv'})
    rows = list(csv.reader(io.StringIO(csv_response.content.decode('utf-8'), newline=''), delimiter=';'))
    assert len(rows) == 2
    assert rows[1][1] == '—' and rows[1][4] == '—' and rows[1][5] == 'результата нет'


def test_run_report_and_audit_report_share_render_primitives(clients, monkeypatch):
    from fastapi.responses import Response

    from testpilot.ai import api as ai_api
    from testpilot.web import audit_report, report_render, run_report

    assert audit_report.csv_document is report_render.csv_document
    assert run_report.csv_document is report_render.csv_document
    assert audit_report.markdown_cell is report_render.markdown_cell
    assert run_report.attachment is report_render.attachment

    c, project, run = _executed_suite(clients, monkeypatch)
    seen = []

    def spy(suite, project_name, format):
        seen.append((suite.get('suite_id'), project_name, format))
        return Response('rendered', media_type='text/plain')

    monkeypatch.setattr(ai_api, 'render_run_report', spy)
    response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report", params={'format': 'csv'})

    assert response.status_code == 200 and response.text == 'rendered'
    assert seen == [(run['suite_id'], project['name'], 'csv')]


def _suite_with_two_cases(clients, monkeypatch):
    impact, c, project, p = _project_with_package(clients)
    assert impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={'odata_url': 'https://1c.example.test/odata/standard.odata'},
    ).status_code == 200
    created = _create_demo_suite(c, p, project['id'])
    assert c.post(
        f"/api/v1/ai/suites/{created['suite_id']}/approve",
        json={'reason': 'Два кейса', 'selected_proposals': [0]},
    ).status_code == 200

    from testpilot.ai import executor as executor_module

    def fake_execute(suite, profile):
        return ([
            {'proposal_index': 0, 'case_index': 0, 'engine': 'odata', 'title': 'Первый кейс',
             'status': 'passed', 'message': 'Коллекция прочитана', 'duration_seconds': 0.25, 'started_at': 'unit'},
            {'proposal_index': 0, 'case_index': 1, 'engine': 'odata', 'title': 'Второй кейс',
             'status': 'failed', 'message': 'Расхождение: ожидалось 2 записи, получено 1',
             'duration_seconds': 0.5, 'started_at': 'unit'},
        ], [(0, b'\x89PNG-unit')])

    monkeypatch.setattr(executor_module, 'execute_1c', fake_execute)
    run = c.post(f"/api/v1/ai/suites/{created['suite_id']}/run-1c")
    assert run.status_code == 200, run.text
    return c, project, run.json()


def test_run_report_has_one_row_per_case_and_matches_counts(clients, monkeypatch):
    c, project, run = _suite_with_two_cases(clients, monkeypatch)
    assert len(run['results']) == 2
    assert sum(run['counts'].values()) == len(run['results'])

    csv_response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report", params={'format': 'csv'})
    assert csv_response.status_code == 200, csv_response.text
    rows = list(csv.reader(io.StringIO(csv_response.content.decode('utf-8'), newline=''), delimiter=';'))
    data_rows = rows[1:]
    assert len(data_rows) == len(run['results']) == 2
    assert [row[1] for row in data_rows] == ['1', '2']
    assert [row[4] for row in data_rows] == ['passed', 'failed']
    assert data_rows[1][5] == 'Расхождение: ожидалось 2 записи, получено 1'
    statuses = [row[4] for row in data_rows]
    for status, count in run['counts'].items():
        assert statuses.count(status) == count

    md_response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report")
    body = md_response.text
    assert 'case 2' in body
    assert 'Расхождение: ожидалось 2 записи, получено 1' in body
    assert '- Расхождений: 1' in body


def test_mutating_step_detector_marks_only_risky_actions():
    from testpilot.ai.plan_guard import MUTATING_CLICK_MARKERS, step_is_mutating

    for marker in MUTATING_CLICK_MARKERS:
        assert step_is_mutating('click', f'  {marker.lower()}  ')
    assert step_is_mutating('fill', '#quantity')
    assert not step_is_mutating('click', 'Продажи')
    assert not step_is_mutating('assert_text', 'Записать')
    assert not step_is_mutating('wait', '')


def _mutating_suite(clients, monkeypatch, steps, *, called=None):
    impact, c = clients
    p = package()
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    project = impact.post('/api/v1/projects', json={'name': 'Изменяющие шаги', 'description': ''}).json()
    assert impact.put(
        f"/api/v1/projects/{project['id']}/connection",
        json={'odata_url': 'https://1c.example.test/odata/standard.odata'},
    ).status_code == 200
    suggestion = _suite_plan(p, 'playwright', [{'steps': steps}])
    monkeypatch.setattr(provider, 'generate', lambda *a, **kw: (suggestion, 'mock-model'))
    response = c.post('/api/v1/ai/suites', json={
        'package_id': p['package_id'],
        'project_id': project['id'],
        'provider': 'llm',
        'consent': True,
    })
    assert response.status_code == 200, response.text
    suite = response.json()
    assert c.post(
        f"/api/v1/ai/suites/{suite['suite_id']}/approve",
        json={'reason': 'Изменяющие шаги', 'selected_proposals': [0]},
    ).status_code == 200

    from testpilot.ai import executor as executor_module

    def fake_execute(target_suite, profile):
        if called is not None:
            called.append(target_suite['suite_id'])
        return ([{'proposal_index': 0, 'case_index': 0, 'engine': 'playwright', 'title': 'Проверка',
                  'status': 'passed', 'message': 'Выполнено', 'duration_seconds': 0.1,
                  'started_at': 'unit'}], [])

    monkeypatch.setattr(executor_module, 'execute_1c', fake_execute)
    return c, project, suite


def test_run_allows_plan_without_mutating_steps(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'click', 'target': 'Продажи'}], called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed'
    assert response.json()['allow_mutations'] is False
    assert called == [suite['suite_id']]


def test_run_blocks_mutating_click_without_consent(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'assert_text', 'target': 'Заказы'},
                               {'action': 'click', 'target': 'Записать'}], called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    assert response.status_code == 422, response.text
    message = response.json()['error']['message']
    assert "Предложение 1: шаг 2 'Записать' потенциально изменяет данные" in message
    assert 'передайте allow_mutations=true, если база тестовая' in message
    assert called == [], 'изменяющий шаг не должен доходить до исполнителя'


def test_run_with_allow_mutations_executes_and_records_consent(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'click', 'target': 'Провести'}], called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c", params={'allow_mutations': 'true'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'completed'
    assert body['allow_mutations'] is True
    assert called == [suite['suite_id']]
    assert c.get(f"/api/v1/ai/suites/{suite['suite_id']}").json()['allow_mutations'] is True


def test_fill_step_requires_consent(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'fill', 'target': '#quantity', 'value': '5'}], called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c")
    assert response.status_code == 422, response.text
    assert "шаг 1 '#quantity' потенциально изменяет данные" in response.json()['error']['message']
    assert called == []


def test_rerun_respects_mutation_consent(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'click', 'target': 'Удалить'}], called=called)
    run = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c", params={'allow_mutations': 'true'})
    assert run.status_code == 200, run.text
    called.clear()

    blocked = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/rerun")
    assert blocked.status_code == 422, blocked.text
    assert "шаг 1 'Удалить' потенциально изменяет данные" in blocked.json()['error']['message']
    assert called == []

    allowed = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/rerun", params={'allow_mutations': 'true'})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['suite_id'] != suite['suite_id']
    assert allowed.json()['allow_mutations'] is True
    assert called == [allowed.json()['suite_id']]


def test_plan_marks_mutating_proposals(clients, monkeypatch):
    c, project, suite = _mutating_suite(clients, monkeypatch, [{'action': 'click', 'target': 'Записать'}])
    assert suite['suggestion']['proposals'][0]['mutating'] is True

    stored = c.get(f"/api/v1/ai/suites/{suite['suite_id']}").json()
    assert stored['suggestion']['proposals'][0]['mutating'] is True

    c2, project2, suite2 = _mutating_suite(clients, monkeypatch, [{'action': 'click', 'target': 'Продажи'}])
    assert suite2['suggestion']['proposals'][0]['mutating'] is False
    assert c2.get(f"/api/v1/ai/suites/{suite2['suite_id']}").json()['suggestion']['proposals'][0]['mutating'] is False


def test_fill_proposal_is_marked_mutating(clients, monkeypatch):
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'fill', 'target': '#quantity', 'value': '5'}])
    assert suite['suggestion']['proposals'][0]['mutating'] is True


def test_history_reports_mutating_count(clients, monkeypatch):
    c, project, suite = _mutating_suite(clients, monkeypatch, [{'action': 'click', 'target': 'Провести'}])
    rows = c.get('/api/v1/ai/suites', params={'project_id': project['id']}).json()
    assert rows[0]['suite_id'] == suite['suite_id']
    assert rows[0]['mutating_count'] == 1

    c2, project2, safe_suite = _mutating_suite(clients, monkeypatch, [{'action': 'click', 'target': 'Продажи'}])
    safe_rows = c2.get('/api/v1/ai/suites', params={'project_id': project2['id']}).json()
    assert safe_rows[0]['mutating_count'] == 0


def test_strict_mode_blocks_click_on_navigation_plan(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch, [{'action': 'click', 'target': 'Продажи'}], called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c", params={'strict_read_only': 'true'})
    assert response.status_code == 422, response.text
    message = response.json()['error']['message']
    assert 'Предложение 1: шаг 1' in message
    assert "действие 'click' недопустимо в строгом режиме" in message
    assert 'assert_text' in message and 'strict_read_only' in message
    assert called == []


def test_strict_mode_allows_read_only_plan(clients, monkeypatch):
    called = []
    c, project, suite = _mutating_suite(
        clients, monkeypatch,
        [{'action': 'assert_text', 'target': 'Продажи'}, {'action': 'wait', 'value': 1000}],
        called=called)

    response = c.post(f"/api/v1/ai/suites/{suite['suite_id']}/run-1c", params={'strict_read_only': 'true'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'completed'
    assert body['strict_read_only'] is True
    assert body['allow_mutations'] is False
    assert called == [suite['suite_id']]
    assert c.get(f"/api/v1/ai/suites/{suite['suite_id']}").json()['strict_read_only'] is True


def test_run_report_explains_odata_404_without_changing_status(clients, monkeypatch):
    c, project, run = _executed_suite(clients, monkeypatch)
    response = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report")
    assert response.status_code == 200, response.text
    body = response.text
    hint = 'Возможная причина: EntitySet не опубликован, неверный base URL/путь или имя сущности'
    assert body.count(hint) == 1
    assert 'пояснение:' in body
    assert '$metadata' in body and 'имя сущности' in body
    assert run['results'][1]['status'] == 'failed'

    csv_body = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report", params={'format': 'csv'}).text
    assert hint not in csv_body, 'пояснение добавляется только в markdown'


def test_run_report_has_no_odata_hint_for_passed_results(clients, monkeypatch):
    c, project, run = _suite_with_two_cases(clients, monkeypatch)
    body = c.get(f"/api/v1/ai/suites/{run['suite_id']}/report").text
    assert [r['status'] for r in run['results']] == ['passed', 'failed']
    assert 'Возможная причина: EntitySet не опубликован' not in body


def test_llm_settings_write_only_to_working_directory_env(clients, monkeypatch, tmp_path):
    """PUT настроек пишет .env текущего каталога и не трогает файл репозитория."""
    import pathlib

    _, c = clients
    repo_env = pathlib.Path('.env').resolve()
    before = repo_env.read_bytes() if repo_env.exists() else None
    (tmp_path / '.env').write_text('TP_LLM_MODEL=old-model\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)

    response = c.put('/api/v1/ai/settings/llm', json={
        'base_url': 'https://llm.example.test/v1',
        'model': 'unit-model',
        'api_key': 'unit-key',
    })

    assert response.status_code == 200, response.text
    written = tmp_path / '.env'
    assert written.is_file()
    assert 'TP_LLM_MODEL=unit-model' in written.read_text(encoding='utf-8')
    assert (repo_env.read_bytes() if repo_env.exists() else None) == before, 'реальный .env изменён'


def test_llm_settings_cannot_write_repository_env(clients, monkeypatch, tmp_path):
    """Даже из каталога репозитория запись уходит только в подменённый целевой путь."""
    import pathlib

    from testpilot.ai import api as ai_api

    _, c = clients
    repo_env = pathlib.Path('.env').resolve()
    before = repo_env.read_bytes() if repo_env.exists() else None
    target = tmp_path / 'redirected.env'
    target.write_text('TP_LLM_MODEL=old-model\n', encoding='utf-8')
    real_path = pathlib.Path

    def redirected(value='.'):
        candidate = real_path(value)
        return target if candidate.name == '.env' else candidate

    monkeypatch.setattr(ai_api, 'Path', redirected)

    response = c.put('/api/v1/ai/settings/llm', json={
        'base_url': 'https://llm.example.test/v1',
        'model': 'unit-model',
        'api_key': 'unit-key',
    })

    assert response.status_code == 200, response.text
    assert target.is_file()
    assert 'TP_LLM_MODEL=unit-model' in target.read_text(encoding='utf-8')
    assert (repo_env.read_bytes() if repo_env.exists() else None) == before, 'реальный .env изменён'
