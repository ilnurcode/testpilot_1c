from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response
from sqlalchemy import select
from testpilot.common import analyst,expert,user,require
from testpilot.db import session,AISuiteRow,PackageRow,TestRow,CoverageRow,EvidenceRow,audit,uid
from testpilot.contracts import now,RunInput
from testpilot.ai.models import GenerateRequest,Decision
from testpilot.ai import provider
from testpilot.ai.executor import execute,browser_path

router=APIRouter(prefix='/api/v1/ai',tags=['Нейросеть и проверки'])

@router.get('/status')
def status(actor=Depends(user)):
    return {**provider.status(),'demo_available':True,'browser_available':bool(browser_path()),'executor_1c_connected':False}

@router.post('/suites')
def create(q:GenerateRequest,actor=Depends(analyst),s=Depends(session,scope='function')):
    package=require(s.get(PackageRow,q.package_id)).payload
    if not package['affected_objects']:raise HTTPException(422,'Область влияния пуста: нет объектов для генерации проверок')
    catalog=[t for t in s.scalars(select(TestRow).where(TestRow.configuration_id==package['configuration_id'],TestRow.active==True)) if t.payload['synthetic']==package['synthetic'] and (not t.payload['compatible_versions'] or package['target_version'] in t.payload['compatible_versions'])]
    rows=[{'test_id':t.id,'title':t.payload['title'],'mandatory':t.mandatory,'object_ids':[c.object_id for c in s.scalars(select(CoverageRow).where(CoverageRow.test_id==t.id,CoverageRow.state=='confirmed'))]} for t in catalog]
    if q.provider=='llm':
        if not q.consent:raise HTTPException(422,'Подтвердите передачу контекста проекта внешнему провайдеру')
        suggestion,model=provider.generate(package,rows,q.object_names,q.model_context)
    else:suggestion=provider.demo_suggestion(package);model=None
    valid_ids={t.id for t in catalog}
    if not set(suggestion.selected_test_ids)<=valid_ids:raise HTTPException(502,'Нейросеть предложила неизвестные или несовместимые тесты')
    for proposal in suggestion.proposals:
        if not set(proposal.object_ids)<=set(package['affected_objects']):raise HTTPException(502,'Нейросеть сослалась на объекты вне области анализа')
    mandatory={t.id for t in catalog if t.mandatory}
    missing=mandatory-set(suggestion.selected_test_ids)
    suggestion.selected_test_ids=sorted(set(suggestion.selected_test_ids)|mandatory)
    if missing:suggestion.warnings.append('Сервер добавил обязательные тесты, пропущенные моделью.')
    pid=uid();payload={'suite_id':pid,'project_id':q.project_id,'package_id':q.package_id,'configuration_id':package['configuration_id'],'target_version':package['target_version'],'synthetic':package['synthetic'],'provider':q.provider,'model':model,'created_at':now(),'suggestion':suggestion.model_dump(),'status':'draft','results':[],'warnings':package['warnings'],'affected_objects':package['affected_objects'],'approval':None}
    s.add(AISuiteRow(id=pid,package_id=q.package_id,project_id=q.project_id,status='draft',payload=payload));audit(s,'intelligence',actor,'generate_suite',pid,{'provider':q.provider,'model':model})
    return payload

@router.get('/suites')
def suites(project_id:str|None=None,actor=Depends(user),s=Depends(session,scope='function')):
    query=select(AISuiteRow).order_by(AISuiteRow.created_at.desc())
    if project_id:query=query.where(AISuiteRow.project_id==project_id)
    return [r.payload for r in s.scalars(query.limit(100))]

@router.get('/suites/{sid}')
def get_suite(sid:str,actor=Depends(user),s=Depends(session,scope='function')):return require(s.get(AISuiteRow,sid)).payload

@router.post('/suites/{sid}/approve')
def approve(sid:str,q:Decision,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    if row.status!='draft':raise HTTPException(409,'План уже подтверждён; создайте новую генерацию для изменения')
    if any(i<0 or i>=len(row.payload['suggestion']['proposals']) for i in q.selected_proposals):raise HTTPException(422,'Неизвестный сценарий')
    generated={}
    for index in sorted(set(q.selected_proposals)):
        proposal=row.payload['suggestion']['proposals'][index]
        tid=f'ai-{sid}-{index}'
        payload={'test_id':tid,'title':proposal['title'],'description':proposal['rationale']+'\n'+'\n'.join(proposal['steps']),
                 'active':True,'criticality':'medium','mandatory':False,'duration_seconds':30*max(len(proposal['cases']),1),
                 'configuration_id':row.payload['configuration_id'],'compatible_versions':[row.payload['target_version']],
                 'executor_parameters':{'engine':proposal['engine'],'cases':proposal['cases'],'adapter':'synthetic_sales' if row.payload['configuration_id']=='presentation-sales' else 'not_connected'},
                 'metadata':{'suite_id':sid,'provider':row.payload['provider'],'model':row.payload['model'],'estimated_duration':True},'synthetic':row.payload['synthetic']}
        s.add(TestRow(id=tid,configuration_id=payload['configuration_id'],active=True,mandatory=False,criticality='medium',duration_seconds=payload['duration_seconds'],payload=payload));s.flush()
        for oid in proposal['object_ids']:
            s.add(CoverageRow(test_id=tid,object_id=oid,state='confirmed',source='ai+expert' if row.payload['provider']=='llm' else 'synthetic+expert',actor=actor,reason=q.reason,updated_at=now()))
        generated[str(index)]=tid
    data={**row.payload,'catalog_test_ids':generated,'status':'approved','approval':{'actor':actor,'at':now(),'reason':q.reason,'selected_proposals':sorted(set(q.selected_proposals))}}
    row.status='approved';row.payload=data;audit(s,'intelligence',actor,'approve_suite',sid,{'reason':q.reason})
    return data

@router.post('/suites/{sid}/run-demo')
def run_demo(sid:str,request:Request,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    if row.status=='completed':return row.payload
    if row.status!='approved':raise HTTPException(409,'Сначала подтвердите сценарии и тестовые данные')
    if not row.payload['synthetic'] or row.payload['configuration_id']!='presentation-sales':raise HTTPException(409,'Этот исполнитель работает только с отдельным учебным стендом. Подключение реальной 1С отсутствует.')
    results,shots=execute(row.payload,request.headers['authorization'])
    for index,content in shots:
        eid=uid();s.add(EvidenceRow(id=eid,suite_id=sid,content=content));results[index]['evidence_id']=eid
    counts={state:sum(r['status']==state for r in results) for state in ('passed','failed','error','skipped')}
    if row.payload.get('catalog_test_ids'):
        history=[];defects=[]
        for key,tid in row.payload['catalog_test_ids'].items():
            subset=[r for r in results if r['proposal_index']==int(key)]
            state=next((x for x in ('error','failed','passed','skipped') if any(r['status']==x for r in subset)),'skipped')
            history.append({'test_id':tid,'status':state,'duration_seconds':sum(r['duration_seconds'] for r in subset),'started_at':min(r['started_at'] for r in subset),'finished_at':max(r['finished_at'] for r in subset),'error':'Расхождение на синтетическом стенде' if state in ('error','failed') else '', 'artifacts':[]})
            if state=='failed':defects.append({'defect_id':f'ai-defect-{sid}-{key}','test_id':tid,'severity':'medium','state':'open','description':'Учебный дефект: оптовый заказ теряет скидку; ожидаемая и фактическая суммы различаются.'})
        from testpilot.intelligence.api import save_run
        save_run(s,RunInput.model_validate({'run_id':'ai-run-'+sid,'configuration_id':row.payload['configuration_id'],'version':row.payload['target_version'],'synthetic':True,'results':history,'defects':defects}),actor)

    data={**row.payload,'status':'completed','results':results,'counts':counts,'finished_at':now(),'executor':'synthetic_sales_http_and_playwright','executor_1c_connected':False}
    row.status='completed';row.payload=data;audit(s,'intelligence',actor,'execute_demo_suite',sid,counts)
    return data

@router.get('/evidence/{eid}')
def evidence(eid:str,actor=Depends(user),s=Depends(session,scope='function')):
    e=require(s.get(EvidenceRow,eid));return Response(e.content,media_type='image/png',headers={'X-Content-Type-Options':'nosniff'})
