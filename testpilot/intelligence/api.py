import csv
import io
from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from testpilot.common import app, user, analyst, expert, runner, require
from testpilot.contracts import (ImpactPackage, TestCase, CoverageInput, RunInput, PlanRequest, Approval, Weights, JobReport, checksum, now, Text, Model, ExecutionJob)
from testpilot.db import (session, PackageRow, TestRow, CoverageRow, RunRow, ResultRow, DefectRow, SettingsRow, PlanRow, PriorityRow, MLRow, JobRow, AuditRow, audit, uid)
from testpilot.intelligence.algorithms import statistics, score_test, optimize, coverage_summary
from testpilot.intelligence import ml

app=app('TestPilot 1C — формирование регрессии')

def test_payload(row): return {**row.payload,'active':row.active,'mandatory':row.mandatory}

def history(s,configuration_id,synthetic):
    runs=list(s.scalars(select(RunRow).where(RunRow.configuration_id==configuration_id,RunRow.synthetic==synthetic)))
    ids=[r.id for r in runs]
    results=list(s.scalars(select(ResultRow).where(ResultRow.run_id.in_(ids)).order_by(ResultRow.started_at,ResultRow.test_id))) if ids else []
    defects=list(s.scalars(select(DefectRow).where(DefectRow.run_id.in_(ids)))) if ids else []
    return results,defects

def result_dict(r):return {**r.payload,'started_at':r.started_at,'finished_at':r.finished_at}

@app.post('/api/v1/impact-packages')
def accept(p:ImpactPackage,actor=Depends(analyst),s=Depends(session,scope="function")):
    old=s.get(PackageRow,p.package_id)
    if old:
        if old.checksum!=p.checksum: raise HTTPException(409,'Идентификатор пакета занят другим содержимым')
        return {'package_id':p.package_id,'idempotent':True}
    s.add(PackageRow(id=p.package_id,checksum=p.checksum,configuration_id=p.configuration_id,payload=p.model_dump(mode='json')))
    audit(s,'intelligence',actor,'accept_impact',p.package_id)
    return {'package_id':p.package_id,'idempotent':False}

@app.get('/api/v1/impact-packages')
def packages(actor=Depends(user),s=Depends(session,scope="function")):
    return [r.payload for r in s.scalars(select(PackageRow))]

@app.get('/api/v1/tests')
def tests(actor=Depends(user),s=Depends(session,scope="function")):
    return [test_payload(r) for r in s.scalars(select(TestRow).order_by(TestRow.id))]

@app.post('/api/v1/tests')
def create_test(t:TestCase,actor=Depends(analyst),s=Depends(session,scope="function")):
    if s.get(TestRow,t.test_id): raise HTTPException(409,'Тест уже существует')
    d=t.model_dump(mode='json')
    if t.criticality=='critical':d['mandatory']=True
    r=TestRow(id=t.test_id,configuration_id=t.configuration_id,active=t.active,mandatory=d['mandatory'],criticality=t.criticality,duration_seconds=t.duration_seconds,payload=d)
    s.add(r);audit(s,'intelligence',actor,'create_test',r.id)
    return d

@app.get('/api/v1/tests/{tid}')
def get_test(tid:str,actor=Depends(user),s=Depends(session,scope="function")):
    return test_payload(require(s.get(TestRow,tid)))

@app.put('/api/v1/tests/{tid}')
def update_test(tid:str,t:TestCase,reason:Text|None=None,actor=Depends(analyst),s=Depends(session,scope="function")):
    r=require(s.get(TestRow,tid))
    if tid!=t.test_id: raise HTTPException(422,'Нельзя менять test_id')
    if t.configuration_id!=r.configuration_id or t.synthetic!=r.payload['synthetic']: raise HTTPException(409,'Конфигурация и происхождение теста неизменяемы')
    if (t.criticality=='critical' and not t.mandatory) or (r.criticality=='critical' and (not t.mandatory or not t.active or t.criticality!='critical')):
        if actor!='expert' or not reason: raise HTTPException(403,'Снятие обязательности critical требует эксперта и обоснования')
    before=test_payload(r)
    r.payload=t.model_dump(mode='json');r.active=t.active;r.mandatory=t.mandatory;r.criticality=t.criticality;r.duration_seconds=t.duration_seconds
    audit(s,'intelligence',actor,'update_test',tid,{'before':before,'after':r.payload,'reason':reason})
    return r.payload

@app.delete('/api/v1/tests/{tid}')
def delete_test(tid:str,actor=Depends(expert),s=Depends(session,scope="function")):
    r=require(s.get(TestRow,tid));r.active=False;r.payload={**r.payload,'active':False}
    audit(s,'intelligence',actor,'archive_test',tid)
    return {'test_id':tid,'active':False,'message':'Архивирован; история и планы сохранены'}

@app.get('/api/v1/coverage')
def coverage(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'test_id':r.test_id,'object_id':r.object_id,'state':r.state,'source':r.source,'actor':r.actor,'reason':r.reason,'updated_at':r.updated_at} for r in s.scalars(select(CoverageRow).order_by(CoverageRow.test_id,CoverageRow.object_id))]

@app.put('/api/v1/tests/{tid}/coverage')
def mapping(tid:str,q:CoverageInput,actor=Depends(analyst),s=Depends(session,scope="function")):
    require(s.get(TestRow,tid))
    if q.state in ('confirmed','rejected') and actor!='expert':raise HTTPException(403,'Подтверждение/отклонение требует эксперта')
    r=s.get(CoverageRow,(tid,q.object_id));before=r.state if r else None
    if not r:r=CoverageRow(test_id=tid,object_id=q.object_id);s.add(r)
    r.state=q.state;r.source=q.source;r.actor=actor;r.reason=q.reason;r.updated_at=now()
    audit(s,'intelligence',actor,'coverage',tid,{'object_id':q.object_id,'before':before,'after':q.state,'reason':q.reason})
    return {'test_id':tid,**q.model_dump(),'actor':actor,'updated_at':r.updated_at}

def save_run(s,q,actor):
    digest=checksum(q.model_dump(mode='json'));old=s.get(RunRow,q.run_id)
    if old:
        if old.checksum!=digest:raise HTTPException(409,'Прогон уже существует с другим содержимым')
        return {'run_id':q.run_id,'idempotent':True}
    for result in q.results:
        t=require(s.get(TestRow,result.test_id),'Неизвестный тест')
        if t.configuration_id!=q.configuration_id or t.payload['synthetic']!=q.synthetic:raise HTTPException(422,'Несовместимая конфигурация или происхождение истории')
        if t.payload['compatible_versions'] and q.version not in t.payload['compatible_versions']:raise HTTPException(422,'История для несовместимой версии')
        if result.finished_at>datetime.now(timezone.utc):raise HTTPException(422,'Результаты из будущего запрещены')
    for d in q.defects:
        if s.get(DefectRow,d.defect_id):raise HTTPException(409,'Дефект с этим ID уже зарегистрирован')
    s.add(RunRow(id=q.run_id,configuration_id=q.configuration_id,version=q.version,synthetic=q.synthetic,checksum=digest));s.flush()
    for r in q.results:s.add(ResultRow(run_id=q.run_id,test_id=r.test_id,status=r.status,duration_seconds=r.duration_seconds,started_at=r.started_at.isoformat(),finished_at=r.finished_at.isoformat(),payload=r.model_dump(mode='json')))
    for d in q.defects:s.add(DefectRow(id=d.defect_id,run_id=q.run_id,**d.model_dump(exclude={'defect_id'})))
    audit(s,'intelligence',actor,'import_history',q.run_id,{'synthetic':q.synthetic})
    return {'run_id':q.run_id,'idempotent':False}

@app.post('/api/v1/history')
def import_history(q:RunInput,actor=Depends(runner),s=Depends(session,scope="function")):
    return save_run(s,q,actor)

@app.get('/api/v1/history')
def runs(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'run_id':r.id,'configuration_id':r.configuration_id,'version':r.version,'synthetic':r.synthetic,'created_at':r.created_at,'results':[result_dict(z) for z in s.scalars(select(ResultRow).where(ResultRow.run_id==r.id))]} for r in s.scalars(select(RunRow).order_by(RunRow.created_at.desc()).limit(200))]

@app.get('/api/v1/defects')
def defects(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'defect_id':d.id,'run_id':d.run_id,'test_id':d.test_id,'severity':d.severity,'state':d.state,'description':d.description,'synthetic':s.get(RunRow,d.run_id).synthetic} for d in s.scalars(select(DefectRow).limit(1000))]

@app.get('/api/v1/settings/weights')
def weights(actor=Depends(user),s=Depends(session,scope="function")):
    r=s.get(SettingsRow,'weights');return r.payload if r else Weights().model_dump()

@app.put('/api/v1/settings/weights')
def set_weights(q:Weights,actor=Depends(expert),s=Depends(session,scope="function")):
    s.merge(SettingsRow(id='weights',payload=q.model_dump()));audit(s,'intelligence',actor,'weights','weights',q.model_dump());return q

class TrainRequest(Model):
    configuration_id: Text
    synthetic: bool = False

@app.post('/api/v1/ml/train')
def train(q:TrainRequest,actor=Depends(expert),s=Depends(session,scope="function")):
    ts=[test_payload(t) for t in s.scalars(select(TestRow).where(TestRow.configuration_id==q.configuration_id)) if t.payload['synthetic']==q.synthetic]
    rs,_=history(s,q.configuration_id,q.synthetic)
    data={**ml.train(ts,[result_dict(r) for r in rs]),'synthetic':q.synthetic,'trained_at':now()}
    r=MLRow(configuration_id=q.configuration_id,payload=data);s.add(r);s.flush();audit(s,'intelligence',actor,'train_ml',r.id)
    return {'model_id':r.id,**data}

@app.get('/api/v1/ml/models')
def models(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'model_id':r.id,'configuration_id':r.configuration_id,**r.payload} for r in s.scalars(select(MLRow).order_by(MLRow.created_at.desc()))]

@app.post('/api/v1/plans')
def create_plan(q:PlanRequest,actor=Depends(analyst),s=Depends(session,scope="function")):
    p=require(s.get(PackageRow,q.package_id)).payload;affected=set(p['affected_objects'])
    candidates=[];excluded=[];unavailable=[]
    mappings=list(s.scalars(select(CoverageRow)))
    rs,ds=history(s,p['configuration_id'],p['synthetic'])
    setting=s.get(SettingsRow,'weights');w=Weights.model_validate(setting.payload).model_dump() if setting else Weights().model_dump()
    model_row=s.scalar(select(MLRow).where(MLRow.configuration_id==p['configuration_id']).order_by(MLRow.created_at.desc()))
    model=model_row.payload if model_row and model_row.payload['synthetic']==p['synthetic'] else None
    ml_reason='ML не запрошен' if not q.use_ml else (model['reason'] if model else 'Нет обученной модели; базовая оценка')
    for r in s.scalars(select(TestRow).where(TestRow.configuration_id==p['configuration_id']).order_by(TestRow.id)):
        t=test_payload(r)
        reason=None
        if t['synthetic']!=p['synthetic']:reason='Различное происхождение данных'
        elif not t['active']:reason='Тест отключён'
        elif t['compatible_versions'] and p['target_version'] not in t['compatible_versions']:reason='Несовместимая версия'
        if reason:
            excluded.append({'test_id':r.id,'reason':reason})
            if t['mandatory'] and t['synthetic']==p['synthetic']:unavailable.append(r.id)
            continue
        covered={m.object_id for m in mappings if m.test_id==r.id and m.state=='confirmed'}
        results=[result_dict(z) for z in rs if z.test_id==r.id]
        defect_data=[{'severity':d.severity} for d in ds if d.test_id==r.id]
        stats=statistics(results,defect_data,t['duration_seconds'])
        prediction=ml.predict(model,t,results) if q.use_ml else None
        candidates.append(score_test(t,covered,affected,stats,w,prediction))
    result=optimize(candidates,affected,q.budget_seconds,q.min_coverage,unavailable)
    pid=uid();result.update({'plan_id':pid,'package_id':q.package_id,'configuration_id':p['configuration_id'],'target_version':p['target_version'],'synthetic':p['synthetic'],'created_at':now(),'excluded_tests':excluded,'weights':w,'ml_status':ml_reason,'model_id':model_row.id if q.use_ml and model and model.get('accepted') else None,'approved':False,'executor_status':'Исполнитель 1С не подключён','mapping_warnings':[{'test_id':m.test_id,'object_id':m.object_id,'state':m.state} for m in mappings if m.object_id in affected and m.state!='confirmed'],'impact_warnings':p['warnings']})
    s.add(PlanRow(id=pid,package_id=q.package_id,payload=result));s.flush()
    for r in candidates:s.add(PriorityRow(plan_id=pid,test_id=r['test_id'],score=r['score'],factors=r))
    audit(s,'intelligence',actor,'create_plan',pid,{'status':result['status']})
    return result

@app.get('/api/v1/plans')
def plans(actor=Depends(user),s=Depends(session,scope="function")):
    return [{'plan_id':r.id,'created_at':r.created_at,'approved':r.approved,'status':r.payload['status'],'configuration_id':r.payload['configuration_id'],'duration_seconds':r.payload['duration_seconds'],'coverage_available':r.payload['coverage_available']} for r in s.scalars(select(PlanRow).order_by(PlanRow.created_at.desc()))]

@app.get('/api/v1/plans/{pid}')
def plan(pid:str,actor=Depends(user),s=Depends(session,scope="function")):
    r=require(s.get(PlanRow,pid));return {**r.payload,'approved':r.approved}

@app.post('/api/v1/plans/{pid}/approve')
def approve(pid:str,q:Approval,actor=Depends(expert),s=Depends(session,scope="function")):
    r=require(s.get(PlanRow,pid));data=dict(r.payload)
    if r.approved:raise HTTPException(409,'Подтверждённый план неизменяем; создайте новый')
    rows=data['ranked_tests'];known={x['test_id'] for x in rows};selected=set(q.selected_ids if q.selected_ids is not None else data['selected_ids'])
    if not selected<=known: raise HTTPException(422,'Выбраны неизвестные или недоступные тесты')
    summary=coverage_summary(rows,selected,set(require(s.get(PackageRow,r.package_id)).payload['affected_objects']))
    violations=[]
    missing=[x['test_id'] for x in rows if x['mandatory'] and x['test_id'] not in selected]
    if missing: violations.append({'code':'mandatory_excluded','test_ids':missing})
    duration=sum(x['duration_seconds'] for x in rows if x['test_id'] in selected)
    if duration>data['budget_seconds']:violations.append({'code':'over_budget','excess_seconds':duration-data['budget_seconds']})
    if summary['coverage_available']<data['min_coverage']:violations.append({'code':'coverage_unreachable'})
    violations.extend(c for c in data['conflicts'] if c['code'] in ('mandatory_unavailable','empty_impact','no_confirmed_coverage'))
    if violations and not q.allow_deviation:raise HTTPException(409,{'message':'Нужно явное подтверждение отклонений','violations':violations})
    data.update(summary);data.update({'selected_ids':[x['test_id'] for x in rows if x['test_id'] in selected],'duration_seconds':duration,'status':'conflict' if violations else 'feasible','conflicts':violations,'approved':True,'approval':{'actor':actor,'at':now(),'reason':q.reason,'deviation':bool(violations)},'original_selected_ids':r.payload['selected_ids']})
    for row in rows:
        row['selected']=row['test_id'] in selected
        row['explanation']+='; выбор подтверждён экспертом' if row['selected'] else '; исключён из подтверждённого плана'
    data['ranked_tests']=rows;r.payload=data;r.approved=True
    audit(s,'intelligence',actor,'approve_plan',pid,{'reason':q.reason,'violations':violations,'selected_ids':sorted(selected)})
    return data

@app.get('/api/v1/plans/{pid}/export')
def plan_export(pid:str,format:str='json',actor=Depends(user),s=Depends(session,scope="function")):
    r=require(s.get(PlanRow,pid))
    if format=='json':return {**r.payload,'approved':r.approved}
    if format!='csv':raise HTTPException(422,'Поддерживаются json и csv')
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['Тест','Название','Приоритет','Длительность','Выбран','Обоснование'])
    for t in r.payload['ranked_tests']:
        # Не допускаем CSV formula injection при открытии в таблицах.
        safe=lambda v: "'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else str(v)
        writer.writerow([safe(t['test_id']),safe(t['title']),t['score'],t['duration_seconds'],t['selected'],safe(t['explanation'])])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=plan.csv'})

@app.post('/api/v1/plans/{pid}/execution-jobs')
def execution_job(pid:str,actor=Depends(expert),s=Depends(session,scope="function")):
    r=require(s.get(PlanRow,pid))
    if not r.approved:raise HTTPException(409,'Требуется подтверждение эксперта')
    jid=uid();data={'schema_version':'1.0','execution_job_id':jid,'test_plan_id':pid,'configuration_id':r.payload['configuration_id'],'version':r.payload['target_version'],'synthetic':r.payload['synthetic'],'created_at':now(),'started_at':None,'finished_at':None,'status':'awaiting_external_runner','executor_connected':False,'tests':[]}
    for tid in r.payload['selected_ids']:
        t=require(s.get(TestRow,tid));d=test_payload(t)
        if not t.active or (d['compatible_versions'] and data['version'] not in d['compatible_versions']):raise HTTPException(409,'Каталог изменился: выбранный тест недоступен, создайте новый план')
        data['tests'].append({'test_id':tid,'parameters':d['executor_parameters']})
    ExecutionJob.model_validate(data)
    s.add(JobRow(id=jid,plan_id=pid,payload=data));audit(s,'intelligence',actor,'export_job',jid)
    return data

@app.get('/api/v1/execution-jobs/{jid}')
def get_job(jid:str,actor=Depends(user),s=Depends(session,scope="function")):
    return require(s.get(JobRow,jid)).payload

@app.post('/api/v1/execution-results')
def receive_results(q:JobReport,actor=Depends(runner),s=Depends(session,scope="function")):
    r=require(s.get(JobRow,q.execution_job_id));data=r.payload;run=q.run
    ids={t['test_id'] for t in data['tests']}
    if {z.test_id for z in run.results}!=ids:raise HTTPException(422,'Результаты должны точно соответствовать составу задания')
    if run.configuration_id!=data['configuration_id'] or run.version!=data['version'] or run.synthetic!=data['synthetic']:raise HTTPException(422,'Версия или происхождение не соответствует заданию')
    digest=checksum(q.model_dump(mode='json'))
    if r.report_checksum and r.report_checksum!=digest:raise HTTPException(409,'Отчёт задания уже зафиксирован')
    result=save_run(s,run,actor)
    r.report_checksum=digest;r.payload={**data,'status':'reported','started_at':min(x.started_at.isoformat() for x in run.results),'finished_at':max(x.finished_at.isoformat() for x in run.results),'run_id':run.run_id}
    audit(s,'intelligence',actor,'receive_results',r.id)
    return result

@app.get('/api/v1/audit')
def audits(actor=Depends(expert),s=Depends(session,scope="function")):
    return [{'actor':r.actor,'action':r.action,'entity_id':r.entity_id,'created_at':r.created_at,'details':r.details} for r in s.scalars(select(AuditRow).where(AuditRow.service=='intelligence').order_by(AuditRow.created_at.desc()).limit(300))]

from testpilot.ai.api import router as ai_router
app.include_router(ai_router)
