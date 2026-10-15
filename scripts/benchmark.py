"""Контрольные синтетические замеры, а не гарантия SLA реальной 1С."""
import json
import platform
import time
from copy import deepcopy
from testpilot.impact.algorithms import analyze
from testpilot.intelligence.algorithms import optimize

def main():
    n=10000
    base={'configuration_id':'benchmark','version':'1','synthetic':True,'objects':[{'object_id':f'n{i:05}','type':'common_module','full_name':f'Узел{i}','parent_id':None,'attributes':{'v':1}} for i in range(n)],'edges':[{'source':f'n{i:05}','target':f'n{i+1:05}','type':'calls','origin':'synthetic','confidence':1} for i in range(n-1)]}
    target=deepcopy(base);target['version']='2';target['objects'][-1]['attributes']['v']=2
    start=time.perf_counter();_,p=analyze(base,target,{'edge_types':['calls'],'max_depth':100,'direction':'impact'});graph_time=time.perf_counter()-start
    star=deepcopy(base);star['edges']=[{**e,'target':f'n{n-1:05}'} for e in base['edges']]
    star_target=deepcopy(star);star_target['version']='2';star_target['objects'][-1]['attributes']['v']=2
    start=time.perf_counter();_,full=analyze(star,star_target,{'edge_types':['calls'],'max_depth':100,'direction':'impact'});star_time=time.perf_counter()-start
    assert len(full['affected_objects'])==10000
    rows=[{'test_id':f't{i:04}','covered_objects':[f'o{i%500}',f'o{(i+1)%500}'],'duration_seconds':10+(i%30),'mandatory':i<2,'score':.2+(i%8)/10} for i in range(1000)]
    start=time.perf_counter();plan=optimize(rows,{f'o{i}' for i in range(500)},7000,.9);plan_time=time.perf_counter()-start
    result={'synthetic':True,'python':platform.python_version(),'platform':platform.platform(),'graph_nodes':n,'graph_edges':n-1,'changed_seeds':1,'max_depth':100,'graph_seconds':graph_time,'affected_count':len(p['affected_objects']),'star_graph_seconds':star_time,'star_affected_count':len(full['affected_objects']),'tests':1000,'impact_objects':500,'plan_seconds':plan_time,'method':plan['optimization_method'],'coverage_available':plan['coverage_available'],'plan_status':plan['status'],'note':'Линейный граф: глубина 100, 101 затронутый; звезда: вся зона 10000. Один источник; не доказывает SLA многих источников и реальной 1С.'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert graph_time<60 and star_time<60 and plan_time<30
if __name__=='__main__':main()
