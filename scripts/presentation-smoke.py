"""Полный фактический цикл на synthetic-стенде. Сеть нейросети и реальная 1С не используются."""
import json
import os
from pathlib import Path
import httpx

with httpx.Client(auth=('expert',os.environ['TP_EXPERT_PASSWORD']),timeout=120) as c:
    def call(method,url,**kwargs):
        r=c.request(method,url,**kwargs);r.raise_for_status();return r.json()
    web=os.environ.get('WEB_URL','http://127.0.0.1:8000');impact=os.environ.get('IMPACT_URL','http://127.0.0.1:8001');ti=os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002')
    p=call('POST',web+'/api/presentation');a=p['analysis'];aid=a['analysis_id']
    call('POST',impact+f'/api/v1/analyses/{aid}/deliver')
    s=call('POST',ti+'/api/v1/ai/suites',json={'package_id':aid,'project_id':p['project_id'],'provider':'demo'})
    s=call('POST',ti+f'/api/v1/ai/suites/{s["suite_id"]}/approve',json={'reason':'Проверены учебные данные и ожидаемые суммы','selected_proposals':[0,1]})
    s=call('POST',ti+f'/api/v1/ai/suites/{s["suite_id"]}/run-demo')
    assert s['counts']=={'passed':2,'failed':2,'error':0,'skipped':0},s
    assert len(s['catalog_test_ids'])==2
    history=call('GET',ti+'/api/v1/history')
    run=next(r for r in history if r['run_id']=='ai-run-'+s['suite_id'])
    assert len(run['results'])==2 and all(r['status']=='failed' for r in run['results'])
    defects=call('GET',ti+'/api/v1/defects')
    assert len([d for d in defects if d['run_id']==run['run_id']])==2
    assert all(r['cleanup_succeeded'] for r in s['results'])
    shots=[r['evidence_id'] for r in s['results'] if r.get('evidence_id')]
    assert len(shots)==2
    for eid in shots:
        r=c.get(ti+'/api/v1/ai/evidence/'+eid);r.raise_for_status();assert r.content.startswith(b'\x89PNG')
    assert call('GET',ti+'/api/v1/ai/suites/'+s['suite_id'])['counts']==s['counts']
    Path('.runtime').mkdir(exist_ok=True)
    Path('.runtime/presentation-result.json').write_text(json.dumps({'project_id':p['project_id'],'suite_id':s['suite_id'],'counts':s['counts'],'evidence_count':len(shots),'synthetic':True},indent=2))
    print(json.dumps({'project_id':p['project_id'],'suite_id':s['suite_id'],'counts':s['counts'],'screenshots':len(shots),'synthetic':True,'real_1c_connected':False},ensure_ascii=False,indent=2))
