from copy import deepcopy
import math
import pytest
from pydantic import ValidationError
from testpilot.contracts import Weights
from testpilot.intelligence.algorithms import statistics, score_test, optimize
from testpilot.intelligence.ml import train, predict
from testpilot.demo import catalog,histories

def row(id,covered,duration=10,mandatory=False,score=.5):
    return {'test_id':id,'covered_objects':covered,'duration_seconds':duration,'mandatory':mandatory,'score':score}

def test_score_math_and_defaults():
    t=catalog()[0][0];stats=statistics([],[],t['duration_seconds']);w=Weights().model_dump()
    r=score_test(t,{'A'},{'A','B'},stats,w)
    expected=.35*.5+.2*.1+.15*0+.2*1+.1/(1+70/300)
    assert math.isclose(r['score'],expected)
    assert stats['risk_default'] and not stats['sufficient']
    assert 0<=r['score']<=1

def test_weights_normalized():
    w=Weights(coverage=2,risk=1,defects=0,criticality=0,cost=0).model_dump();assert sum(w.values())==1
    with pytest.raises(ValidationError):Weights(coverage=-1)
    with pytest.raises(ValidationError):Weights(coverage=0,risk=0,defects=0,criticality=0,cost=0)

def test_history_stats():
    s=statistics([{'status':'passed','duration_seconds':10},{'status':'failed','duration_seconds':30},{'status':'skipped','duration_seconds':0}],[{'severity':'critical'}],5)
    assert s['failure_rate']==.5 and s['median_duration']==20 and s['defect_frequency']==.5

def test_mandatory_over_budget():
    p=optimize([row('A',['x'],20,True),row('B',['y'],10)],{'x','y'},5,.9)
    assert p['selected_ids']==['A'] and p['status']=='conflict' and p['duration_seconds']==20
    assert any(c['code']=='mandatory_over_budget' for c in p['conflicts'])

def test_union_coverage_exact():
    p=optimize([row('A',['x']),row('B',['x']),row('C',['y'])],{'x','y','z'},20,1)
    assert p['selected_ids']==['A','C']
    assert p['coverage_available']==1 and p['coverage_all']==2/3
    assert p['uncovered_objects']==['z'] and p['denominator_available']==2

def test_exact_beats_greedy_trap():
    p=optimize([row('A',['a','b','c'],4),row('B',['a','b'],3),row('C',['c','d'],3)],{'a','b','c','d'},6,1)
    assert p['selected_ids']==['B','C'] and p['status']=='feasible'

def test_zero_coverage_unreachable():
    p=optimize([row('A',[])],{'x'},10,.9)
    assert p['coverage_available']==0 and p['status']=='conflict'
    p=optimize([row('A',['x'],10)],{'x'},0,.9)
    assert p['status']=='conflict' and p['selected_ids']==[]

def test_budget_changes_and_stability():
    rows=[row('A',['x']),row('B',['y']),row('C',['z'])]
    a=optimize(deepcopy(rows),{'x','y','z'},10,1);b=optimize(deepcopy(rows),{'x','y','z'},30,1)
    assert len(a['selected_ids'])==1 and len(b['selected_ids'])==3
    assert a==optimize(deepcopy(rows),{'x','y','z'},10,1)

def test_greedy_large_and_unavailable_mandatory():
    rows=[row(str(i),[str(i)],1) for i in range(30)]
    p=optimize(rows,{str(i) for i in range(30)},10,.9,['disabled'])
    assert p['optimization_method']=='greedy' and len(p['selected_ids'])==10
    assert p['status']=='conflict' and p['conflicts'][0]['code']=='mandatory_unavailable'

def test_ml_fallback_and_real_training():
    tests=[t for t,_,_ in catalog()];events=[r for h in histories() for r in h['results']]
    assert not train(tests,[])['accepted']
    model=train(tests,events)
    assert model['train_rows']>0 and model['validation_rows']>0
    assert model['train_end']<model['validation_start']
    assert 0<=model['auc']<=1
    if model['accepted']:assert 0<=predict(model,tests[0],events)<=1
    else:assert predict(model,tests[0],events) is None
    all_pass=[{**r,'status':'passed'} for r in events]
    assert not train(tests,all_pass)['accepted']

def test_nonfinite_metadata_rejected():
    from testpilot.contracts import TestCase
    with pytest.raises(ValidationError):TestCase.model_validate({**catalog()[0][0],'metadata':{'invalid':float('nan')}})

def test_ml_accepted_model_predicts_and_is_json_serializable():
    from datetime import datetime,timedelta,timezone
    import json
    ts=[{**catalog()[0][0],'test_id':'high','criticality':'critical','duration_seconds':50},
        {**catalog()[0][0],'test_id':'low','criticality':'low','duration_seconds':50}]
    events=[]
    for day in range(60):
        stamp=datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(days=day)
        for tid,status in [('high','failed'),('low','passed')]:
            events.append({'test_id':tid,'status':status,'duration_seconds':50,'started_at':stamp.isoformat(),'finished_at':(stamp+timedelta(seconds=50)).isoformat()})
    model=train(ts,events)
    assert model['accepted'] and model['auc']>=.65 and model['balanced_accuracy']>=.55
    restored=json.loads(json.dumps(model))
    high=predict(restored,ts[0],[r for r in events if r['test_id']=='high'])
    low=predict(restored,ts[1],[r for r in events if r['test_id']=='low'])
    assert high>low and 0<=low<=high<=1
