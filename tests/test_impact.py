from copy import deepcopy
import pytest
from pydantic import ValidationError
from testpilot.contracts import Configuration, ImpactPackage
from testpilot.impact.algorithms import analyze, compare
from testpilot.demo import configurations

def payload(objects=('A','B','C','D'),edges=(('A','B'),('B','C'))):
    return {'schema_version':'1.0','configuration_id':'unit','version':'1','source':'synthetic','synthetic':True,
            'objects':[{'object_id':x,'type':'common_module','full_name':x,'parent_id':None,'attributes':{'revision':1}} for x in objects],
            'edges':[{'source':a,'target':b,'type':'calls','origin':'synthetic','confidence':1.0} for a,b in edges]}

def calculation(base,changed,depth=10,**params):
    target=deepcopy(base);target['version']='2'
    for o in target['objects']:
        if o['object_id'] in changed:o['attributes']['revision']=2
    return analyze(base,target,{'edge_types':['calls','uses','belongs_to','subscribes_to'],'max_depth':depth,'direction':'impact',**params})[1]

def test_direction():
    p=calculation(payload(),['B'])
    assert p['affected_objects']==['A','B']
    path=next(x for x in p['dependency_paths'] if x['affected_object']=='A')
    assert path['nodes']==['B','A'] and path['edges'][0]['source']=='A'

def test_line():
    p=calculation(payload(),['C']);assert p['affected_objects']==['A','B','C']
    assert next(x['depth'] for x in p['dependency_paths'] if x['affected_object']=='A')==2

def test_branch_cycle_multiple():
    base=payload(edges=(('A','B'),('B','C'),('C','A'),('D','B')))
    p=calculation(base,['B','C']);assert p['affected_objects']==['A','B','C','D']
    assert {x['changed_object'] for x in p['dependency_paths'] if x['affected_object']=='D'}=={'B','C'}
    assert len(p['dependency_paths'])<20

@pytest.mark.parametrize('depth,expected',[(0,['C']),(1,['B','C']),(2,['A','B','C'])])
def test_max_depth(depth,expected):assert calculation(payload(),['C'],depth)['affected_objects']==expected

def test_alternative_explanations():
    base=payload(objects=('A','B','C','D'),edges=(('A','B'),('A','C'),('B','D'),('C','D')))
    paths=[p['nodes'] for p in calculation(base,['D'])['dependency_paths'] if p['affected_object']=='A']
    assert paths==[['D','B','A'],['D','C','A']]

def test_removed_base_graph():
    base=payload();target=deepcopy(base);target['version']='2';target['objects']=[o for o in target['objects'] if o['object_id']!='C'];target['edges']=[e for e in target['edges'] if e['target']!='C']
    changes,p=analyze(base,target,{'edge_types':['calls'],'max_depth':10,'direction':'impact'})
    assert set(p['affected_objects'])=={'A','B','C'}
    path=next(x for x in p['dependency_paths'] if x['changed_object']=='C' and x['affected_object']=='A')
    assert all(e['version']=='1' for e in path['edges'])

def test_unknown_and_no_edges():
    base=payload();base['edges'][0]['type']='unknown'
    p=calculation(base,['B']);assert p['affected_objects']==['B'];assert any('unknown' in w for w in p['warnings'])
    assert calculation(payload(edges=()),['B'])['affected_objects']==['B']

def test_canonical_attributes_order():
    a=payload();a['objects'][0]['attributes']={'x':1,'y':2}
    b=deepcopy(a);b['objects'][0]['attributes']={'y':2,'x':1}
    assert all(x['status']=='unchanged' for x in compare(a,b))
    assert Configuration.model_validate(a).content()==Configuration.model_validate(b).content()

def test_edge_only_change():
    a=payload();b=deepcopy(a);b['edges'][0]['confidence']=.5
    c=next(c for c in compare(a,b) if c['object_id']=='A')
    assert c['status']=='modified' and c['properties']==['edges']

def test_deterministic():
    a=calculation(payload(),['C']);b=calculation(payload(),['C'])
    for key in ['affected_objects','changed_objects','dependency_paths','warnings']:assert a[key]==b[key]

def test_contract_checksum_and_references():
    p=calculation(payload(),['C']);ImpactPackage.model_validate(p)
    p['affected_objects'].append('unknown')
    with pytest.raises(ValidationError):ImpactPackage.model_validate(p)

@pytest.mark.parametrize('mutation',[lambda d:d['objects'].append(d['objects'][0]),lambda d:d['edges'][0].update(target='missing'),lambda d:d.update(schema_version='2'),lambda d:d['edges'][0].update(type='invalid'),lambda d:d['objects'][0].update(full_name='\x00')])
def test_invalid_configuration(mutation):
    d=payload();mutation(d)
    with pytest.raises(ValidationError):Configuration.model_validate(d)

def test_import_idempotency_conflict_and_rollback(clients):
    a,_=clients;p=payload()
    first=a.post('/api/v1/imports',json=p);assert first.status_code==200
    assert a.post('/api/v1/imports',json=p).json()['idempotent']
    bad=deepcopy(p);bad['objects'].append(bad['objects'][0]);assert a.post('/api/v1/imports',json=bad).status_code==422
    changed=deepcopy(p);changed['objects'][0]['full_name']='Новое';assert a.post('/api/v1/imports',json=changed).status_code==409
    assert len(a.get('/api/v1/versions').json())==1
    p['version']='2';p['objects'][1]['attributes']['revision']=2
    assert a.post('/api/v1/imports',json=p).status_code==200
    r=a.post('/api/v1/analyses',json={'configuration_id':'unit','base_version':'1','target_version':'2'})
    assert r.status_code==200,r.text
    d=r.json();assert d['affected_objects']==['A','B']
    assert a.get('/api/v1/analyses/'+d['analysis_id']+'/export').json()==d
    assert len(a.get('/api/v1/graph?configuration_id=unit&version_name=2&limit=2').json()['nodes'])==2

def test_same_version_and_missing(clients):
    a,_=clients
    assert a.post('/api/v1/compare',json={'configuration_id':'x','base_version':'1','target_version':'1'}).status_code==422
    assert a.get('/api/v1/analyses/missing').status_code==404

def test_auth_and_size(clients):
    a,_=clients
    assert a.get('/api/v1/versions',auth=('wrong','wrong')).status_code==401
    assert a.post('/api/v1/imports',content=b' '* (8*1024*1024+1)).status_code==413

def test_adapters_are_explicitly_unsupported():
    from testpilot.impact.adapters import NormalizedJSONAdapter,UnsupportedXMLAdapter,FutureBSLAdapter
    import json
    assert NormalizedJSONAdapter().parse(json.dumps(payload()).encode()).configuration_id=='unit'
    with pytest.raises(NotImplementedError):UnsupportedXMLAdapter().parse(b'<xml/>')
    with pytest.raises(NotImplementedError):FutureBSLAdapter().extract({})

def test_references_checksum_for_raw_bytes(clients):
    from testpilot.db import VersionRow
    from sqlalchemy import select
    import hashlib,json
    from testpilot import db
    a,_=clients;raw=json.dumps(payload(),indent=2).encode()
    assert a.post('/api/v1/imports',content=raw).status_code==200
    with db._factory() as s:
        row=s.scalar(select(VersionRow));assert row.raw_checksum==hashlib.sha256(raw).hexdigest()
    reordered=payload();reordered['objects'].reverse();reordered['edges'].reverse()
    assert a.post('/api/v1/imports',json=reordered).json()['idempotent']
