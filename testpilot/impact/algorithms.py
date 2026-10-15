from collections import deque
import networkx as nx
from testpilot.contracts import checksum, now
from testpilot.db import uid

# Оригинал: потребитель -> зависимость. Принадлежность: ребёнок -> родитель.
# Изменение родителя потенциально затрагивает ребёнка; подписчик зависит от события.
RULES={'calls':'reverse','uses':'reverse','belongs_to':'reverse','subscribes_to':'reverse'}

def compare(base,target):
    a={o['object_id']:o for o in base['objects']};b={o['object_id']:o for o in target['objects']}
    def index(d):
        out={}
        for e in d['edges']: out.setdefault(e['source'],[]).append(e)
        return {k:sorted(v,key=lambda e:(e['target'],e['type'])) for k,v in out.items()}
    ae,be=index(base),index(target)
    changes=[]
    for oid in sorted(a.keys()|b.keys()):
        props=[]
        if oid not in a: status='added'
        elif oid not in b: status='removed'
        else:
            props=[k for k in sorted(a[oid].keys()|b[oid].keys()) if a[oid].get(k)!=b[oid].get(k)]
            if ae.get(oid,[])!=be.get(oid,[]): props.append('edges')
            status='modified' if props else 'unchanged'
        changes.append({'object_id':oid,'status':status,'properties':props})
    return changes

def graph(payload,edge_types,direction):
    g=nx.MultiDiGraph()
    g.add_nodes_from(o['object_id'] for o in payload['objects'])
    for e in sorted(payload['edges'],key=lambda e:(e['source'],e['target'],e['type'])):
        if e['type'] not in RULES or e['type'] not in edge_types: continue
        u,v=e['source'],e['target']
        if direction=='impact': u,v=v,u
        g.add_edge(u,v,key=e['type'],edge={**e,'version':payload['version']})
    return g

def analyze(base,target,params,analysis_id=None):
    changes=compare(base,target);changed=[c for c in changes if c['status']!='unchanged']
    warnings=['Нормализованные входные данные: полнота динамических и внешних зависимостей не гарантируется.']
    unknown=sorted({e['type'] for d in (base,target) for e in d['edges'] if e['type'] not in RULES})
    if unknown: warnings.append('Неподдерживаемые связи (не обходятся): '+', '.join(unknown))
    if not changed: warnings.append('Изменений нет: зона влияния пуста.')
    if not target['edges'] and not base['edges']: warnings.append('Граф не содержит связей.')
    gs={False:graph(target,params['edge_types'],params['direction']),True:graph(base,params['edge_types'],params['direction'])}
    affected=set(); paths=[];truncated=False
    for change in changed:
        seed=change['object_id'];g=gs[change['status']=='removed']
        affected.add(seed)
        # BFS хранит до трёх кратчайших альтернатив для каждой пары источник/объект.
        distance={seed:0}; queue=deque([(seed,[seed],[])])
        counts={seed:1}
        while queue:
            u,nodes,edges=queue.popleft()
            if len(paths)>=20000: truncated=True; break
            paths.append({'changed_object':seed,'affected_object':u,'nodes':nodes,'edges':edges,'depth':len(edges),'confirmed':all(e['confidence']==1 for e in edges)})
            if len(edges)>=params['max_depth']: continue
            for _,v,key,data in sorted(g.out_edges(u,keys=True,data=True),key=lambda e:(e[1],e[2])):
                depth=len(edges)+1
                if v in nodes: continue
                if v not in distance: distance[v]=depth
                if distance[v]!=depth: continue
                if counts.get(v,0)>=3:
                    truncated=True; continue
                counts[v]=counts.get(v,0)+1
                affected.add(v);queue.append((v,nodes+[v],edges+[data['edge']]))
        if len(paths)>=20000: break
    # Даже при лимите объяснений все исходные изменения сохраняются.
    affected.update(c['object_id'] for c in changed)
    if truncated: warnings.append('Лимит объяснений: до 3 кратчайших путей на причину/объект и 20000 путей; результат может быть неполным.')
    aid=analysis_id or uid()
    p={'schema_version':'1.0','package_id':aid,'configuration_id':base['configuration_id'],'base_version':base['version'],'target_version':target['version'],'analysis_id':aid,'created_at':now(),'synthetic':base['synthetic'] or target['synthetic'],'changed_objects':changed,'affected_objects':sorted(affected),'dependency_paths':paths,'warnings':warnings}
    p['checksum']=checksum(p)
    return changes,p
