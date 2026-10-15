import json
import httpx
import pytest
from fastapi import HTTPException
from testpilot.ai import provider
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

@pytest.mark.parametrize('reply',[{'choices':[]},{'choices':[{'message':{'content':'not json'}}]},{'choices':[{'message':{'content':'{"summary":"x","proposals":[]}'}}]}])
def test_invalid_model_response_is_rejected(monkeypatch,reply):
    monkeypatch.setenv('TP_LLM_API_KEY','unit')
    with pytest.raises(HTTPException) as e:provider.generate(package(),[],{},transport=httpx.MockTransport(lambda r:httpx.Response(200,json=reply)))
    assert e.value.status_code==502

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

def test_consent_required_before_external_request(clients):
    _,c=clients;p=package();c.post('/api/v1/impact-packages',json=p)
    r=c.post('/api/v1/ai/suites',json={'package_id':p['package_id'],'project_id':'unit','provider':'llm','consent':False})
    assert r.status_code==422

def test_unknown_objects_and_tests_rejected(clients,monkeypatch):
    _,c=clients;p=package();c.post('/api/v1/impact-packages',json=p)
    suggestion=provider.demo_suggestion(p);suggestion.proposals[0].object_ids=['missing']
    monkeypatch.setattr(provider,'generate',lambda *a,**k:(suggestion,'test'))
    body={'package_id':p['package_id'],'project_id':'unit','provider':'llm','consent':True}
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
