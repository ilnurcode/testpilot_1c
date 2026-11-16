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
    _,c=clients;p=package();assert c.post('/api/v1/impact-packages',json=p).status_code==200
    r=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':'unit-project','provider':'demo'})
    assert r.status_code==200,r.text
    s=r.json();sid=s['suite_id'];assert s['provider']=='demo' and len(s['suggestion']['proposals'])==2
    assert c.post(f'/api/v1/ai/suites/{sid}/run-demo').status_code==409
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Test','selected_proposals':[99]}).status_code==422
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Test','selected_proposals':[0]},auth=('analyst','test-password-only')).status_code==403
    assert c.post(f'/api/v1/ai/suites/{sid}/approve',json={'reason':'Проверено','selected_proposals':[0]}).status_code==200
    assert c.get(f'/api/v1/ai/suites/{sid}').json()['status']=='approved'
    assert len(c.get('/api/v1/ai/suites?project_id=unit-project').json())==1

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


def test_llm_settings_api(clients):
    _, c = clients
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
    _, c = clients
    assert c.post('/api/v1/impact-packages', json=p).status_code == 200
    monkeypatch.setattr(provider, 'generate', lambda *a, **kw: (suggestion, 'mock-model'))
    response = c.post('/api/v1/ai/suites', json={
        'package_id': p['package_id'],
        'project_id': 'unit-project',
        'provider': 'llm',
        'consent': True,
    })
    return c, response


def test_suite_rejects_unknown_playwright_action_before_persistence(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'playwright', [{'steps': [{'action': 'hover', 'target': 'Продажи'}]}])
    _, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'hover' in response.text


def test_structurally_valid_mocked_plan_creates_suite(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'playwright', [{'steps': [{'action': 'assert_text', 'target': 'Продажи'}]}])
    _, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'draft'


def test_suite_rejects_odata_case_without_entity_or_metadata_check(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'odata', [{'expected': 'HTTP 200 OK'}])
    _, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'entity' in response.text


def test_suite_accepts_manual_proposal_without_cases(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'manual', [])
    _, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'draft'


def test_suite_rejects_unknown_engine_with_proposal_index(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(p, 'unsupported-engine', [{'entity': 'Document_Заказ'}])
    _, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert 'Предложение 0' in response.text and 'unsupported-engine' in response.text


def test_suite_rejects_generated_open_url_before_persistence(clients, monkeypatch):
    p = package()
    suggestion = _suite_plan(
        p,
        'playwright',
        [{'steps': [{'action': 'open_url', 'target': 'https://evil.example/'}]}],
    )
    c, response = _create_mocked_llm_suite(clients, monkeypatch, p, suggestion)
    assert response.status_code == 422
    assert (
        "Предложение 0, case 0, шаг 0: действие open_url не допускается в сгенерированном плане; "
        "вход в веб-клиент выполняет исполнитель"
    ) in response.text
    assert c.get('/api/v1/ai/suites?project_id=unit-project').json() == []
