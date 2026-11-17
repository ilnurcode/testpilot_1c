import os
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response
from sqlalchemy import select
from testpilot.common import analyst,expert,user,require
from testpilot.db import session,AISuiteRow,PackageRow,TestRow,CoverageRow,EvidenceRow,ProjectRow,audit,uid
from testpilot.contracts import now,RunInput
from testpilot.ai.models import GenerateRequest,Decision,CodexStatus
from testpilot.ai import provider,codex
from testpilot.ai.executor import execute,browser_path
from testpilot.ai.plan_guard import annotate_proposals, mutation_conflicts, mutating_proposal_count, strict_conflicts
from testpilot.web.run_report import render_run_report

router=APIRouter(prefix='/api/v1/ai',tags=['Нейросеть и проверки'])


from pathlib import Path
from testpilot.ai.models import LLMSettingsInput

@router.get('/settings/llm')
def get_llm_settings(actor=Depends(user)):
    st = provider.status()
    base = os.environ.get('TP_LLM_BASE_URL', 'https://api.openai.com/v1')
    model = os.environ.get('TP_LLM_MODEL', 'gpt-4o-mini')
    key = os.environ.get('TP_LLM_API_KEY', '')
    return {
        'base_url': base,
        'model': model,
        'has_key': bool(key),
        'endpoint_host': st.get('endpoint_host'),
        'configured': st.get('configured', False)
    }

def _env_write_allowed():
    """Запись в файл окружения разрешена только явным TP_ALLOW_ENV_WRITE=1."""
    return (os.environ.get('TP_ALLOW_ENV_WRITE') or '').strip().lower() in ('1','true','yes','on')


def _env_file():
    return Path(os.environ.get('TP_ENV_FILE') or '.env')


@router.put('/settings/llm')
def update_llm_settings(q: LLMSettingsInput, actor=Depends(expert)):
    """По умолчанию меняет настройки только в памяти процесса; запись в файл — явный opt-in."""
    if q.base_url:
        os.environ['TP_LLM_BASE_URL'] = q.base_url.strip()
    if q.model:
        os.environ['TP_LLM_MODEL'] = q.model.strip()
    if q.api_key is not None and q.api_key.strip():
        os.environ['TP_LLM_API_KEY'] = q.api_key.strip()

    env_path = _env_file()
    persisted = False
    if q.persist:
        if not _env_write_allowed():
            raise HTTPException(409,'Запись в .env отключена. Установите TP_ALLOW_ENV_WRITE=1 или используйте изменения только в памяти (persist=false).')
        try:
            lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
            def set_var(key, val):
                nonlocal lines
                lines = [l for l in lines if not l.startswith(f"{key}=")]
                lines.append(f"{key}={val}")
            if q.base_url: set_var('TP_LLM_BASE_URL', q.base_url.strip())
            if q.model: set_var('TP_LLM_MODEL', q.model.strip())
            if q.api_key is not None and q.api_key.strip(): set_var('TP_LLM_API_KEY', q.api_key.strip())
            env_path.write_text("\n".join(lines) + "\n", encoding='utf-8')
            persisted = True
        except OSError as error:
            raise HTTPException(500, f'Не удалось записать настройки в {env_path}: {error}')

    data = get_llm_settings(actor)
    data.update({
        'persisted': persisted,
        'env_file': str(env_path),
        'env_write_allowed': _env_write_allowed(),
        'note': ('Настройки записаны в файл окружения.' if persisted else
                 'Изменения только в памяти процесса. Для записи в .env задайте TP_ALLOW_ENV_WRITE=1 и persist=true.'),
    })
    return data

def _codex_status_for(actor):
    result = codex.status()
    if actor != 'expert':
        result = {**result, 'verification_uri': None, 'user_code': None}
    return result


@router.get('/status')
def status(actor=Depends(user)):
    return {**provider.status(),'codex':_codex_status_for(actor),'demo_available':True,'browser_available':bool(browser_path()),'executor_1c_connected':False}

@router.get('/codex/status',response_model=CodexStatus)
def codex_status(actor=Depends(user)):
    return _codex_status_for(actor)

@router.post('/codex/login',response_model=CodexStatus)
def codex_login(actor=Depends(expert)):
    return codex.login()

@router.post('/codex/logout',response_model=CodexStatus)
def codex_logout(actor=Depends(expert)):
    return codex.logout()

def _validate_proposals(suggestion):
    from testpilot.integration.webclient import WebClientRunner

    supported_engines = {'odata', 'playwright', 'manual'}
    for proposal_index, proposal in enumerate(suggestion.proposals):
        engine = proposal.engine
        if engine not in supported_engines:
            raise HTTPException(422, f"Предложение {proposal_index}: неподдерживаемый engine '{engine}'")
        if engine == 'manual':
            continue
        if not proposal.cases:
            raise HTTPException(422, f"Предложение {proposal_index}: для engine '{engine}' нужны cases")
        if engine == 'odata':
            for case_index, case in enumerate(proposal.cases):
                if getattr(case, 'check', None) != 'metadata' and not (case.entity and case.entity.strip()):
                    raise HTTPException(
                        422,
                        f"Предложение {proposal_index}, case {case_index}: для odata укажите entity или check='metadata'",
                    )
            continue
        for case_index, case in enumerate(proposal.cases):
            if not case.steps:
                raise HTTPException(
                    422,
                    f"Предложение {proposal_index}, case {case_index}: для playwright нужны steps",
                )
            for step_index, step in enumerate(case.steps):
                action = step.get('action') if isinstance(step, dict) else None
                if action == 'open_url':
                    raise HTTPException(
                        422,
                        f"Предложение {proposal_index}, case {case_index}, шаг {step_index}: действие open_url не допускается в сгенерированном плане; вход в веб-клиент выполняет исполнитель",
                    )
                if not isinstance(action, str) or action not in WebClientRunner.SUPPORTED_ACTIONS:
                    raise HTTPException(
                        422,
                        f"Предложение {proposal_index}, case {case_index}, шаг {step_index}: неизвестное действие '{action}'",
                    )
                required_target = action in {'click', 'assert_text', 'assert_section', 'fill'}
                if required_target and not (isinstance(step.get('target'), str) and step['target'].strip()):
                    raise HTTPException(
                        422,
                        f"Предложение {proposal_index}, case {case_index}, шаг {step_index}: действие '{action}' требует target",
                    )
                if action in {'wait', 'fill'} and step.get('value') is None:
                    raise HTTPException(
                        422,
                        f"Предложение {proposal_index}, case {case_index}, шаг {step_index}: действие '{action}' требует value",
                    )


@router.post('/suites')
def create(q:GenerateRequest,actor=Depends(analyst),s=Depends(session,scope='function')):
    package=require(s.get(PackageRow,q.package_id)).payload
    if not package['affected_objects']:raise HTTPException(422,'Область влияния пуста: нет объектов для генерации проверок')
    catalog=[t for t in s.scalars(select(TestRow).where(TestRow.configuration_id==package['configuration_id'],TestRow.active==True)) if t.payload['synthetic']==package['synthetic'] and (not t.payload['compatible_versions'] or package['target_version'] in t.payload['compatible_versions'])]
    rows=[{'test_id':t.id,'title':t.payload['title'],'mandatory':t.mandatory,'object_ids':[c.object_id for c in s.scalars(select(CoverageRow).where(CoverageRow.test_id==t.id,CoverageRow.state=='confirmed'))]} for t in catalog]
    if q.provider in ('llm','codex'):
        if not q.consent:raise HTTPException(422,'Подтвердите передачу контекста проекта внешнему провайдеру')
        generator=codex.generate if q.provider=='codex' else provider.generate
        suggestion,model=generator(package,rows,q.object_names,q.model_context)
    elif q.provider=='local':
        suggestion=provider.generate_local_1c_tests(package,rows,q.object_names);model='local-rules'
    else:suggestion=provider.demo_suggestion(package);model=None
    # Synthetic demo plans use the separate demo executor, not WebClientRunner.
    if q.provider != 'demo':
        _validate_proposals(suggestion)
    valid_ids={t.id for t in catalog}
    suite_warnings=list(package['warnings'])
    if catalog:
        if not set(suggestion.selected_test_ids)<=valid_ids:raise HTTPException(502,'Нейросеть предложила неизвестные или несовместимые тесты')
    else:
        # selected_test_ids только ссылается на каталог; сценарии модели проходят отдельную проверку ниже.
        suggestion.selected_test_ids=[]
        warning='Совместимый каталог тестов пуст; выбраны только сценарии предложений модели.'
        if warning not in suggestion.warnings and len(suggestion.warnings)<20:suggestion.warnings.append(warning)
        suite_warnings.append(warning)
    for proposal in suggestion.proposals:
        if not set(proposal.object_ids)<=set(package['affected_objects']):raise HTTPException(502,'Нейросеть сослалась на объекты вне области анализа')
    mandatory={t.id for t in catalog if t.mandatory}
    missing=mandatory-set(suggestion.selected_test_ids)
    suggestion.selected_test_ids=sorted(set(suggestion.selected_test_ids)|mandatory)
    if missing:suggestion.warnings.append('Сервер добавил обязательные тесты, пропущенные моделью.')
    pid=uid();payload={'suite_id':pid,'project_id':q.project_id,'package_id':q.package_id,'configuration_id':package['configuration_id'],'target_version':package['target_version'],'synthetic':package['synthetic'],'provider':q.provider,'model':model,'created_at':now(),'suggestion':suggestion.model_dump(),'status':'draft','results':[],'warnings':suite_warnings,'affected_objects':package['affected_objects'],'approval':None}
    s.add(AISuiteRow(id=pid,package_id=q.package_id,project_id=q.project_id,status='draft',payload=payload));audit(s,'intelligence',actor,'generate_suite',pid,{'provider':q.provider,'model':model})
    return _suite_response(payload)

RUN_COUNT_KEYS=('passed','failed','error','skipped')

def _suite_response(payload):
    """Ответ с пометкой mutating у каждого предложения (расчёт тот же, что у блокировки прогона)."""
    return annotate_proposals(payload)

def _suite_summary(row:AISuiteRow)->dict:
    """Сводка прогона для истории: без suggestion, results и прочих тяжёлых частей payload."""
    payload=row.payload or {};counts=payload.get('counts') or {};approval=payload.get('approval') or {}
    return {
        'suite_id':row.id,
        'package_id':row.package_id,
        'model':payload.get('model'),
        'provider':payload.get('provider'),
        'status':row.status,
        'counts':{key:int(counts.get(key) or 0) for key in RUN_COUNT_KEYS},
        'created_at':row.created_at,
        'finished_at':payload.get('finished_at'),
        'approved_count':len(approval.get('selected_proposals') or []),
        'mutating_count':mutating_proposal_count(payload),
    }

@router.get('/suites')
def suites(project_id:str|None=None,limit:int=20,actor=Depends(user),s=Depends(session,scope='function')):
    """История прогонов: сводка без тяжёлых полей (suggestion/payload) для списков и страницы проекта."""
    if limit<1 or limit>100:raise HTTPException(400,'limit должен быть от 1 до 100')
    query=select(AISuiteRow).order_by(AISuiteRow.created_at.desc(),AISuiteRow.id.desc()).limit(limit)
    if project_id:
        require(s.get(ProjectRow,project_id),'Проект не найден')
        query=query.where(AISuiteRow.project_id==project_id)
    return [_suite_summary(row) for row in s.scalars(query)]

@router.get('/suites/{sid}')
def get_suite(sid:str,actor=Depends(user),s=Depends(session,scope='function')):return _suite_response(require(s.get(AISuiteRow,sid)).payload)

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
            s.add(CoverageRow(test_id=tid,object_id=oid,state='confirmed',source='ai+expert' if row.payload['provider'] in ('llm','codex') else 'synthetic+expert',actor=actor,reason=q.reason,updated_at=now()))
        generated[str(index)]=tid
    data={**row.payload,'catalog_test_ids':generated,'status':'approved','approval':{'actor':actor,'at':now(),'reason':q.reason,'selected_proposals':sorted(set(q.selected_proposals))}}
    row.status='approved';row.payload=data;audit(s,'intelligence',actor,'approve_suite',sid,{'reason':q.reason})
    return _suite_response(data)

@router.post('/suites/{sid}/run-demo')
def run_demo(sid:str,request:Request,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    if row.status=='completed':return _suite_response(row.payload)
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
    return _suite_response(data)




def _profile_for_suite(row,proj):
    from testpilot.integration.probe import Profile
    if proj and proj.web_url and os.environ.get('TP_1C_AUTHORIZATION'):
        selected = row.payload['approval']['selected_proposals']
        proposals = row.payload['suggestion']['proposals']
        needs_web_client = any(proposals[index]['engine'] == 'playwright' for index in selected)
        has_username = proj.auth_username or os.environ.get('TP_1C_USERNAME')
        if needs_web_client and not has_username:
            raise HTTPException(
                422,
                'Для сценариев веб-клиента задайте пользователя и пароль 1С в настройках подключения проекта; Authorization используется только для OData',
            )
    try:
        if proj and (proj.odata_url or proj.web_url):
            odata_url = proj.odata_url or os.environ.get('TP_1C_ODATA_URL', '')
            web_url = proj.web_url or os.environ.get('TP_1C_WEB_URL') or None
            uname = proj.auth_username or os.environ.get('TP_1C_USERNAME') or None
            pwd = (proj.auth_password if proj.auth_password is not None else '') if uname else None
            auth_hdr = os.environ.get('TP_1C_AUTHORIZATION') or None if not uname else None
            return Profile(odata_url=odata_url, web_url=web_url, username=uname, password=pwd, authorization=auth_hdr)
        env_prof = Profile.from_environment()
        return Profile(
            odata_url=env_prof.odata_url,
            web_url=env_prof.web_url,
            username=env_prof.username,
            password=env_prof.password,
            authorization=env_prof.authorization,
            ca_file=env_prof.ca_file,
        )
    except Exception as e:
        raise HTTPException(422, f'Параметры подключения к 1С не настроены: {e}')


def _require_run_mode(suite,allow_mutations,strict_read_only):
    """Строгий режим разрешает только проверки без кликов; иначе — эвристика по подписи кнопки и opt-in."""
    if strict_read_only:
        conflicts=strict_conflicts(suite)
        if conflicts:
            raise HTTPException(422,'; '.join(conflicts))
        return
    if allow_mutations:
        return
    conflicts=mutation_conflicts(suite)
    if conflicts:
        raise HTTPException(422,'; '.join(conflicts))


def _execute_1c_row(row,actor,s,allow_mutations=False,strict_read_only=False):
    """Выполняет утверждённые сценарии строки suite на базе 1С и сохраняет результат в эту же строку."""
    from testpilot.db import ProjectRow
    profile=_profile_for_suite(row,s.get(ProjectRow,row.project_id))
    from testpilot.ai.executor import execute_1c
    results,shots=execute_1c(row.payload, profile)
    for index,content in shots:
        eid=uid();s.add(EvidenceRow(id=eid,suite_id=row.id,content=content));results[index]['evidence_id']=eid
    counts={state:sum(r['status']==state for r in results) for state in ('passed','failed','error','skipped')}
    data={**row.payload,'status':'completed','results':results,'counts':counts,'finished_at':now(),'executor':'1c_odata_and_webclient','executor_1c_connected':True,
          'allow_mutations':bool(allow_mutations) and not strict_read_only,'strict_read_only':bool(strict_read_only)}
    row.status='completed';row.payload=data
    audit(s,'intelligence',actor,'execute_1c_suite',row.id,counts)
    return data


@router.post('/suites/{sid}/run-1c')
def run_1c(sid:str,allow_mutations:bool=False,strict_read_only:bool=False,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    if row.status=='completed':return _suite_response(row.payload)
    if row.status!='approved':raise HTTPException(409,'Сначала подтвердите сценарии и тестовые данные')
    _require_run_mode(row.payload,allow_mutations,strict_read_only)
    return _suite_response(_execute_1c_row(row,actor,s,allow_mutations,strict_read_only))


@router.get('/suites/{sid}/report')
def run_report(sid:str,format:str='md',actor=Depends(user),s=Depends(session,scope='function'))->Response:
    """Отчёт о прогоне сохранённого плана: только чтение сохранённых данных."""
    row=require(s.get(AISuiteRow,sid),'Сохранённый план не найден')
    project=s.get(ProjectRow,row.project_id)
    return render_run_report(row.payload,project.name if project else row.project_id,format)


@router.post('/suites/{sid}/rerun')
def rerun_suite(sid:str,allow_mutations:bool=False,strict_read_only:bool=False,actor=Depends(expert),s=Depends(session,scope='function')):
    """Повторный прогон сохранённого плана: новая запись suite, исходная остаётся в истории нетронутой."""
    source=require(s.get(AISuiteRow,sid),'Сохранённый план не найден')
    approval=(source.payload or {}).get('approval') or {}
    if not approval.get('selected_proposals'):
        raise HTTPException(409,'В плане нет утверждённых сценариев: сначала подтвердите план')
    _require_run_mode(source.payload,allow_mutations,strict_read_only)
    new_id=uid()
    payload={**source.payload,'suite_id':new_id,'created_at':now(),'status':'approved','results':[]}
    for stale in ('counts','finished_at','executor','executor_1c_connected'):
        payload.pop(stale,None)
    row=AISuiteRow(id=new_id,package_id=source.package_id,project_id=source.project_id,status='approved',payload=payload)
    s.add(row);s.flush()
    audit(s,'intelligence',actor,'rerun_suite',new_id,{'source_suite':sid})
    try:
        return _suite_response(_execute_1c_row(row,actor,s,allow_mutations,strict_read_only))
    except Exception as exc:
        message=getattr(exc,'detail',None) or str(exc)
        failed={**payload,'status':'error','message':str(message),'finished_at':now(),
                'allow_mutations':bool(allow_mutations) and not strict_read_only,'strict_read_only':bool(strict_read_only)}
        row.status='error';row.payload=failed
        audit(s,'intelligence',actor,'rerun_suite_failed',new_id,{'source_suite':sid})
        return _suite_response({**failed,'counts':{state:0 for state in ('passed','failed','error','skipped')}})


@router.get('/evidence/{eid}')
def evidence(eid:str,actor=Depends(user),s=Depends(session,scope='function')):
    e=require(s.get(EvidenceRow,eid));return Response(e.content,media_type='image/png',headers={'X-Content-Type-Options':'nosniff'})
