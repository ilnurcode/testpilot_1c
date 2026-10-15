import hashlib
import json
import os
import httpx
from fastapi import Depends, HTTPException, Request, Query
from sqlalchemy import select
from testpilot.common import app, analyst, user, require, upload_body
from testpilot.contracts import Configuration, CompareRequest, AnalysisRequest
from testpilot.db import session, ConfigurationRow, VersionRow, ObjectRow, EdgeRow, AnalysisRow, AffectedRow, AuditRow, audit, uid
from testpilot.contracts import checksum, ImpactPackage
from testpilot.impact.algorithms import analyze, compare

app=app('TestPilot 1C — анализ влияния')

def version(s,cid,version):
    return require(s.scalar(select(VersionRow).where(VersionRow.configuration_id==cid,VersionRow.version==version)),'Версия не найдена')

def pair(s,q):
    if q.base_version==q.target_version: raise HTTPException(422,'Выберите разные версии')
    return version(s,q.configuration_id,q.base_version),version(s,q.configuration_id,q.target_version)

@app.post('/api/v1/imports')
def upload(raw:bytes=Depends(upload_body),actor=Depends(analyst),s=Depends(session,scope="function")):
    from pydantic import ValidationError
    try: model=Configuration.model_validate_json(raw)
    except ValidationError as e: raise HTTPException(422,[{'path':list(x['loc']),'message':x['msg']} for x in e.errors()])
    from testpilot.impact.imports import store_version
    return store_version(s,model,raw,actor)

@app.get('/api/v1/versions')
def versions(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'id':r.id,'configuration_id':r.configuration_id,'version':r.version,'source':r.source,'imported_at':r.imported_at,'checksum':r.checksum,'synthetic':r.payload['synthetic']} for r in s.scalars(select(VersionRow).order_by(VersionRow.imported_at))]

@app.post('/api/v1/compare')
def comparison(q:CompareRequest,actor=Depends(analyst),s=Depends(session,scope="function")):
    a,b=pair(s,q);changes=compare(a.payload,b.payload)
    aid=uid();s.add(AnalysisRow(id=aid,base_id=a.id,target_id=b.id,parameters=q.model_dump(),comparison=changes,package={}))
    audit(s,'impact',actor,'compare',aid)
    return {'comparison_id':aid,'changes':changes}

@app.post('/api/v1/analyses')
def analysis(q:AnalysisRequest,actor=Depends(analyst),s=Depends(session,scope="function")):
    a,b=pair(s,q);changes,p=analyze(a.payload,b.payload,q.model_dump())
    ImpactPackage.model_validate(p)
    r=AnalysisRow(id=p['analysis_id'],base_id=a.id,target_id=b.id,parameters=q.model_dump(),comparison=changes,package=p)
    s.add(r);s.flush()
    for oid in p['affected_objects']:
        s.add(AffectedRow(analysis_id=r.id,object_id=oid,paths=[x for x in p['dependency_paths'] if x['affected_object']==oid]))
    audit(s,'impact',actor,'analyze',r.id)
    return p

@app.get('/api/v1/analyses')
def analyses(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'analysis_id':r.id,'created_at':r.created_at,'parameters':r.parameters,'affected_count':len(r.package.get('affected_objects',[])),'has_package':bool(r.package)} for r in s.scalars(select(AnalysisRow).order_by(AnalysisRow.created_at.desc()))]

@app.get('/api/v1/analyses/{aid}')
def report(aid:str,actor=Depends(user),s=Depends(session,scope="function")):
    r=require(s.get(AnalysisRow,aid))
    snapshots=[s.get(VersionRow,vid).payload for vid in (r.base_id,r.target_id) if vid]
    names={o['object_id']:o['full_name'] for d in snapshots for o in d['objects']}
    affected=set(r.package.get('affected_objects',[]))
    context=[{'object_id':o['object_id'],'type':o['type'],'full_name':o['full_name'],'attributes':{k:str(v)[:5000] for k,v in o['attributes'].items() if k in ('code','description','signature','revision')}} for o in snapshots[-1]['objects'] if o['object_id'] in affected][:100]
    return {'parameters':r.parameters,'changes':r.comparison,'package':r.package,'object_names':names,'model_context':context}

@app.get('/api/v1/analyses/{aid}/export')
def export(aid:str,actor=Depends(user),s=Depends(session,scope="function")):
    p=require(s.get(AnalysisRow,aid)).package
    if not p: raise HTTPException(409,'Это только сравнение; выполните анализ')
    return p

@app.get('/api/v1/graph')
def subgraph(configuration_id:str,version_name:str,limit:int=Query(150,ge=1,le=500),focus:str|None=None,depth:int=Query(2,ge=0,le=10),node_type:str|None=None,edge_type:str|None=None,direction:str=Query('both',pattern='^(both|incoming|outgoing)$'),actor=Depends(user),s=Depends(session,scope="function")):
    d=version(s,configuration_id,version_name).payload
    nodes={o['object_id']:o for o in d['objects']}
    selected=set(nodes)
    if focus:
        if focus not in nodes: raise HTTPException(404,'Узел не найден')
        selected={focus};front={focus}
        for _ in range(depth):
            nxt=set()
            for e in d['edges']:
                if edge_type and e['type']!=edge_type: continue
                if direction in ('both','outgoing') and e['source'] in front:nxt.add(e['target'])
                if direction in ('both','incoming') and e['target'] in front:nxt.add(e['source'])
            front=nxt-selected;selected|=nxt
    if node_type: selected={x for x in selected if nodes[x]['type']==node_type}
    ids=set(sorted(selected)[:limit])
    return {'nodes':[nodes[x] for x in sorted(ids)],'edges':[e for e in d['edges'] if e['source'] in ids and e['target'] in ids and (not edge_type or e['type']==edge_type)],'truncated':len(selected)>limit,'total_nodes':len(nodes),'direction_semantics':'Оригинальные рёбра: потребитель → зависимость; влияние обходится обратно'}

@app.post('/api/v1/analyses/{aid}/deliver')
async def deliver(aid:str,request:Request,actor=Depends(analyst),s=Depends(session,scope="function")):
    p=require(s.get(AnalysisRow,aid)).package
    if not p: raise HTTPException(409,'Сначала выполните анализ')
    # Адрес только из доверенной конфигурации; пользовательские URL не принимаются.
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.post(os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002')+'/api/v1/impact-packages',json=p,headers={'Authorization':request.headers['authorization']})
        if r.status_code>=400: raise HTTPException(502,{'message':'Приём пакета отклонён; анализ сохранён','upstream_status':r.status_code})
    except httpx.HTTPError: raise HTTPException(503,'Второй сервис недоступен; анализ сохранён, отправку можно повторить')
    audit(s,'impact',actor,'deliver',aid)
    return r.json()

@app.get('/api/v1/audit')
def audits(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'actor':r.actor,'action':r.action,'entity_id':r.entity_id,'created_at':r.created_at,'details':r.details} for r in s.scalars(select(AuditRow).where(AuditRow.service=='impact').order_by(AuditRow.created_at.desc()).limit(200))]

from testpilot.impact.projects import router as projects_router
app.include_router(projects_router)
