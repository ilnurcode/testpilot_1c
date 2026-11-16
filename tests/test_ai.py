import json
import httpx
import pytest
from fastapi import HTTPException
from testpilot.ai import provider,codex
from testpilot.ai.models import Suggestion
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def package():return analyze(sample(1),sample(2),{'edge_types':['calls','uses','belongs_to','subscribes_to'],'direction':'impact','max_depth':10})[1]

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
    suggestion=provider.demo_suggestion(p);suggestion.proposals[0].object_ids=['missing']
    module=codex if name=='codex' else provider
    monkeypatch.setattr(module,'generate',lambda *a,**k:(suggestion,'test'))
    body={'package_id':p['package_id'],'project_id':'unit','provider':name,'consent':True}
    assert c.post('/api/v1/ai/suites',json=body).status_code==502
    suggestion.proposals[0].object_ids=p['affected_objects'][:1];suggestion.selected_test_ids=['missing']
    assert c.post('/api/v1/ai/suites',json=body).status_code==502

def test_real_projects_cannot_use_demo_executor(clients,monkeypatch):
    from testpilot.contracts import checksum
    _,c=clients;p=package();suggestion=provider.demo_suggestion(p);p['synthetic']=False
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
    suggestion=provider.demo_suggestion(p)
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
