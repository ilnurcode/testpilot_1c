import asyncio
import os
import threading
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response, JSONResponse
from sqlalchemy import delete, select
from testpilot.common import analyst,expert,user,require
from testpilot.db import (session,AISuiteRow,PackageRow,TestRow,CoverageRow,EvidenceRow,ProjectRow,audit,uid,
                          RunRow,ResultRow,DefectRow,PriorityRow)
from testpilot.contracts import now,RunInput,checksum
from testpilot.ai.models import GenerateRequest,Decision,CodexStatus,Assertion
from testpilot.ai import provider,codex
from testpilot.ai.executor import execute,browser_path
from testpilot.ai.plan_guard import annotate_proposals, mutation_conflicts, mutating_proposal_count, strict_conflicts
from testpilot.web.run_report import render_run_report
from testpilot.ai.login_guard import ensure_login_allowed

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


def _probe_login_context(data: dict, s):
    """Проверки зонда: проект существует и у него настроен web_url. Произвольный URL из тела не принимается."""
    project_id = str(data.get('project_id') or '').strip()
    if not project_id:
        raise HTTPException(422, 'Укажите project_id')
    project = s.get(ProjectRow, project_id)
    if project is None:
        raise HTTPException(404, 'Проект не найден')
    web_url = (project.web_url or '').strip()
    if not web_url:
        raise HTTPException(422, 'У проекта не настроен web_url: заполните подключение и повторите зонд')
    from urllib.parse import urlsplit

    parts = urlsplit(web_url)
    origin = f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else ''
    return project, web_url, origin


def _probe_boot_flag(data: dict) -> bool:
    """boot=true разрешает same-origin non-GET (учётные данные не передаются); по умолчанию только GET/HEAD."""
    value = data.get('boot', False)
    if not isinstance(value, bool):
        raise HTTPException(422, 'Параметр boot должен быть true или false')
    return value


def _probe_login_facts(web_url: str, boot: bool = False) -> dict:
    """Запуск зонда с маппингом ошибок: сбой открытия/origin — 503 с исходным текстом причины."""
    from testpilot.integration.webclient import probe_login_form

    try:
        return probe_login_form(web_url, boot=boot)
    except Exception as error:
        raise HTTPException(503, str(error)) from error


@router.post('/connection/probe-login')
async def probe_login(data: dict, actor=Depends(expert), s=Depends(session, scope='function')):
    """Read-only зонд разметки формы входа: адрес берётся только из web_url проекта, без ввода и кликов."""
    boot = _probe_boot_flag(data)
    project, web_url, origin = _probe_login_context(data, s)
    facts = await asyncio.to_thread(_probe_login_facts, web_url, boot)
    return {'project_id': project.id, 'web_url_origin': origin, **facts}

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

def _plan_revision(payload: dict) -> str:
    return checksum({'suggestion': payload.get('suggestion'), 'selected': (payload.get('approval') or {}).get('selected_proposals') or []})


def _validate_run_assertions(payload: dict) -> None:
    from fastapi import HTTPException
    proposals = (payload.get('suggestion') or {}).get('proposals') or []
    selected = (payload.get('approval') or {}).get('selected_proposals') or []
    for index in selected:
        if not isinstance(index, int) or index < 0 or index >= len(proposals):
            raise HTTPException(409, 'Утверждение ссылается на отсутствующее предложение; требуется повторное согласование')
        proposal = proposals[index]
        engine = proposal.get('engine')
        if engine not in ('odata', 'playwright', 'manual'):
            raise HTTPException(422, f'Неизвестный engine {engine!r}; требуется повторное согласование')
        cases = proposal.get('cases') or []
        if engine in ('odata', 'playwright') and not cases:
            raise HTTPException(422, 'Автоматическое предложение требует cases с обязательной typed-проверкой; требуется повторное согласование')
        for case in cases:
            raw_assertions = case.get('assertions') or []
            parsed = [Assertion.model_validate(item) for item in raw_assertions]
            required = [item for item in parsed if item.required]
            if engine != 'manual' and not required:
                raise HTTPException(422, 'Автоматический case требует обязательную typed-проверку (business, metadata или connectivity); без неё нужен manual-сценарий или повторное согласование')
            for item in parsed:
                if item.scope == 'business' and not item.source_ref:
                    raise HTTPException(422, 'Business assertion требует source_ref, а не HTTP200')


def _target_identity(s, project) -> str:
    """Canonical public identity from A4; no secret/project-only digest."""
    if project is None:
        return ''
    from testpilot.impact.projects import target_identity
    return target_identity(s, project)


def _run_state(row: AISuiteRow) -> dict:
    payload = row.payload or {}
    counts = payload.get('counts') or {}
    return {'suite_id': row.id, 'status': row.status, 'attempt_id': row.attempt_id,
            'attempt_version': row.attempt_version, 'lease_expires_at': row.lease_expires_at,
            'cancel_requested': bool(row.cancel_requested), 'started_at': row.started_at,
            'finished_at': row.finished_at, 'plan_revision': row.plan_revision,
            'counts': {key: int(counts.get(key) or 0) for key in RUN_COUNT_KEYS},
            'business_success': payload.get('business_success'),
            'message': payload.get('message')}


def _enqueue(row: AISuiteRow, s, actor: str, idempotency_key: str | None, login_key: str | None = None, *, allow_mutations: bool = False, strict_read_only: bool = False) -> dict:
    from fastapi import HTTPException
    if idempotency_key and row.idempotency_key == idempotency_key:
        return _run_state(row)
    if row.status in ('queued', 'running'):
        if idempotency_key and row.idempotency_key and row.idempotency_key != idempotency_key:
            raise HTTPException(409, 'У прогона уже есть другой idempotency key')
        return _run_state(row)
    if row.status != 'approved':
        raise HTTPException(409, 'Сначала подтвердите сценарии и тестовые данные')
    _validate_run_assertions(row.payload or {})
    current_revision = _plan_revision(row.payload or {})
    approved = (row.payload or {}).get('approval') or {}
    approved_revision = approved.get('plan_revision')
    if approved_revision != current_revision:
        raise HTTPException(409, 'План изменился после утверждения; требуется повторное согласование')
    project = s.get(ProjectRow, row.project_id)
    if approved.get('target_identity') != _target_identity(s, project):
        raise HTTPException(409, 'Цель/подключение изменились после утверждения; требуется повторное согласование')
    row.status = 'queued'
    row.plan_revision = current_revision
    row.idempotency_key = idempotency_key
    row.cancel_requested = False
    data = {**(row.payload or {}), 'status': 'queued', 'message': None,
            'allow_mutations': bool(allow_mutations) and not strict_read_only, 'strict_read_only': bool(strict_read_only)}
    if login_key:
        data['login_key'] = login_key
        data['cooldown_key'] = login_key
    row.payload = data
    audit(s, 'intelligence', actor, 'enqueue_suite', row.id, {'executor': data.get('executor')})
    return _run_state(row)


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
    project=s.get(ProjectRow,row.project_id)
    approval={'actor':actor,'at':now(),'reason':q.reason,'selected_proposals':sorted(set(q.selected_proposals)),'target_identity':_target_identity(s, project)}
    data={**row.payload,'catalog_test_ids':generated,'status':'approved','approval':approval}
    approval['plan_revision']=checksum({'suggestion':data.get('suggestion'),'selected':approval['selected_proposals']})
    row.plan_revision=approval['plan_revision']
    row.status='approved';row.payload=data;audit(s,'intelligence',actor,'approve_suite',sid,{'reason':q.reason})
    return _suite_response(data)

def _is_demo_plan(payload:dict)->bool:
    """Синтетический demo-план исполняется только demo-исполнителем, не execute_claim."""
    return bool((payload or {}).get('synthetic')) and (payload or {}).get('configuration_id')=='presentation-sales'


DEMO_CASE_INPUTS=('quantity','price','discount','expected_total')


def _demo_plan_shape_error(payload:dict)->str|None:
    """Демо-исполнитель понимает только кейсы учебного стенда: явный отказ вместо error в результате.

    План local/llm на том же синтетическом пакете проходит проверку identity, но его кейсы
    несут typed assertions, а не quantity/price/discount/expected_total. Без этой проверки
    прогон завершался статусом error с сообщением о браузерном шаге.
    """
    proposals=(payload or {}).get('suggestion',{}).get('proposals') or []
    approved=[index for index in ((payload or {}).get('approval') or {}).get('selected_proposals') or []
              if isinstance(index,int) and 0<=index<len(proposals)]
    automatic=[proposals[index] for index in approved if (proposals[index].get('engine') or '')!='manual']
    if not automatic:
        return 'Демо-исполнитель требует утверждённый автоматический кейс учебного стенда; в плане только ручные проверки.'
    for proposal in automatic:
        for case in proposal.get('cases') or [None]:
            if not isinstance(case,dict) or any(case.get(key) is None for key in DEMO_CASE_INPUTS):
                return ('Утверждённый план не содержит учебных данных демонстрации '
                        '(quantity/price/discount/expected_total): демо-исполнитель исполняет только план provider=demo. '
                        'Typed-проверки такого плана исполняются в тестовой 1С через «Запустить проверки в 1С».')
    return None


def _demo_claim(factory,sid:str):
    """Короткая транзакция: проверки и атомарное назначение попытки демо-прогона.

    Блокировка строки suite живёт только здесь; браузерный шаг выполняется уже без неё.
    """
    from testpilot.ai import worker
    with factory() as session:
        row=require(session.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
        if row.status=='completed':return None,_suite_response(row.payload)
        if row.status in ('queued','running'):raise HTTPException(409,'Прогон уже выполняется')
        if row.status!='approved':raise HTTPException(409,'Сначала подтвердите сценарии и тестовые данные')
        if not _is_demo_plan(row.payload):raise HTTPException(409,'Этот исполнитель работает только с отдельным учебным стендом. Подключение реальной 1С отсутствует.')
        shape_error=_demo_plan_shape_error(row.payload)
        if shape_error:raise HTTPException(409,shape_error)
        worker.begin_attempt(row)
        row.status='running'
        row.payload={**row.payload,'demo_claim':True}
        session.commit()
        return {'suite_id':row.id,'payload':dict(row.payload),'attempt_id':row.attempt_id,
                'attempt_version':row.attempt_version},None


def _demo_finish(factory,claim_data:dict,actor:str,*,results=None,shots=(),error:str|None=None):
    """Короткая транзакция: закрыть демо-прогон под fence попытки (устаревшая попытка не пишет)."""
    from testpilot.ai import worker
    with factory() as session:
        row=session.scalar(worker._attempt_query(claim_data).with_for_update())
        if row is None:
            if error is not None:return None
            raise HTTPException(409,'Попытка устарела: результаты демо-прогона не сохранены')
        payload={key:value for key,value in (row.payload or {}).items() if key!='demo_claim'}
        if error is not None:
            payload.update(status='error',message=error[:500],finished_at=now(),business_success=None)
            row.status='error';row.payload=payload;row.finished_at=now();row.lease_expires_at=None
            session.commit()
            return None
        for index,content in shots:
            eid=uid();session.add(EvidenceRow(id=eid,suite_id=row.id,content=content));results[index]['evidence_id']=eid
        counts={state:sum(r['status']==state for r in results) for state in ('passed','failed','error','skipped')}
        if payload.get('catalog_test_ids'):
            history=[];defects=[]
            for key,tid in payload['catalog_test_ids'].items():
                subset=[r for r in results if r['proposal_index']==int(key)]
                state=next((x for x in ('error','failed','passed','skipped') if any(r['status']==x for r in subset)),'skipped')
                history.append({'test_id':tid,'status':state,'duration_seconds':sum(r['duration_seconds'] for r in subset),'started_at':min(r['started_at'] for r in subset),'finished_at':max(r['finished_at'] for r in subset),'error':'Расхождение на синтетическом стенде' if state in ('error','failed') else '', 'artifacts':[]})
                if state=='failed':defects.append({'defect_id':f'ai-defect-{row.id}-{key}','test_id':tid,'severity':'medium','state':'open','description':'Учебный дефект: оптовый заказ теряет скидку; ожидаемая и фактическая суммы различаются.'})
            from testpilot.intelligence.api import save_run
            save_run(session,RunInput.model_validate({'run_id':'ai-run-'+row.id,'configuration_id':payload['configuration_id'],'version':payload['target_version'],'synthetic':True,'results':history,'defects':defects}),actor)
        data={**payload,'status':'completed','results':results,'counts':counts,'finished_at':now(),'executor':'synthetic_sales_http_and_playwright','executor_1c_connected':False,
              'business_success':worker.business_success(results)}
        row.status='completed';row.payload=data;row.lease_expires_at=None
        audit(session,'intelligence',actor,'execute_demo_suite',row.id,counts)
        session.commit()
    return data


@router.post('/suites/{sid}/run-demo')
def run_demo(sid:str,request:Request,actor=Depends(expert)):
    """Демо-прогон: попытка и сохранение — короткими транзакциями, длинный шаг — вне транзакции."""
    from testpilot import db as _db
    from testpilot.ai import worker
    claim_data,done=_demo_claim(_db._factory,sid)
    if done is not None:return done
    stop=threading.Event()
    keeper=threading.Thread(target=worker.heartbeat,args=(_db._factory,claim_data,stop),
                            name='ai-demo-heartbeat',daemon=True)
    keeper.start()
    try:
        results,shots=execute(claim_data['payload'],request.headers['authorization'])
    except Exception as exc:
        _demo_finish(_db._factory,claim_data,actor,error=str(exc) or exc.__class__.__name__)
        raise
    finally:
        stop.set()
        keeper.join(timeout=2)
    return _suite_response(_demo_finish(_db._factory,claim_data,actor,results=results,shots=shots))




CONNECTION_REQUIRED_MESSAGE = ('Задайте подключение проекта: odata_url/web_url; '
                               'env-профиль в прогоне не используется')


def _require_project_connection(proj):
    """Без настроенного подключения проекта запускать нечего; env-профиль не подставляется."""
    if not proj or not (proj.odata_url or proj.web_url):
        raise HTTPException(422, CONNECTION_REQUIRED_MESSAGE)


def _profile_for_suite(row,proj,s=None):
    """Project-scoped connection only: no env fallback in approved runs."""
    from testpilot import secrets
    from testpilot.integration.probe import Profile
    _require_project_connection(proj)
    uname = proj.auth_username
    pwd = None
    if uname:
        from testpilot.impact.projects import load_connection_password
        try:
            pwd, _warning = load_connection_password(s, proj)
        except secrets.SecretsError as e:
            raise HTTPException(503, f'Секрет подключения недоступен: {e}') from e
    if uname and not pwd:
        # Basic Auth без пароля не строится: отказ до прогона, а не ValueError внутри сборки профиля.
        raise HTTPException(422, 'Для входа в 1С задайте пароль в настройках подключения проекта: '
                                 'логин без пароля не используется для Basic Auth')
    try:
        return Profile(odata_url=proj.odata_url or '', web_url=proj.web_url or None,
                       username=uname, password=pwd)
    except ValueError as e:
        raise HTTPException(422, f'Ошибка конфигурации подключения: {e}') from e


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




def suite_preflight_blocker(s,row):
    """Причина, по которой прогон не начнётся без браузера; None — блокеров нет."""
    payload=row.payload or {}
    proposals=(payload.get('suggestion') or {}).get('proposals') or []
    approved=[index for index in ((payload.get('approval') or {}).get('selected_proposals') or [])
              if isinstance(index,int) and 0<=index<len(proposals)]
    if not approved:
        return 'нет утверждённых предложений: подтвердите план перед прогоном'
    project=s.get(ProjectRow,row.project_id)
    web_url=(project.web_url or '').strip() if project else ''
    odata_url=(project.odata_url or '').strip() if project else ''
    target_engines=[(proposals[index].get('engine') or '').strip() for index in approved]
    if not web_url and not odata_url and any(engine in ('playwright','odata') for engine in target_engines):
        # Цели нет вовсе: конкретный «web_url/odata_url не настроен» здесь был бы половиной правды,
        # и пересогласование плана без подключения всё равно не даёт запуска.
        return CONNECTION_REQUIRED_MESSAGE
    for index in approved:
        engine=(proposals[index].get('engine') or '').strip()
        if engine=='playwright' and not web_url:
            return 'web_url не настроен: заполните подключение проекта'
        if engine=='odata' and not odata_url:
            return 'odata_url не настроен: заполните подключение проекта'
    return None


@router.get('/suites/{sid}/preflight')
def suite_preflight(sid:str,actor=Depends(user),s=Depends(session,scope='function')):
    """Read-only preflight: same blocker contract as run; no browser/no writes."""
    from testpilot.audit_limit import MAX_ISSUES_KEPT
    from testpilot.ai import login_guard
    from testpilot.integration.webclient import WebClientRunner
    from testpilot.ai.plan_guard import mutation_conflicts, strict_conflicts
    row=require(s.get(AISuiteRow,sid),'Сохранённый план не найден')
    payload=row.payload or {}
    project=s.get(ProjectRow,row.project_id)
    web_url=(project.web_url or '').strip() if project else ''
    odata_url=(project.odata_url or '').strip() if project else ''
    proposals=(payload.get('suggestion') or {}).get('proposals') or []
    approved=[index for index in ((payload.get('approval') or {}).get('selected_proposals') or [])
              if isinstance(index,int) and 0<=index<len(proposals)]
    blocker=suite_preflight_blocker(s,row)
    warnings=[blocker] if blocker else []
    engines=[]
    for index in approved:
        engine=(proposals[index].get('engine') or '').strip()
        blocked=''
        if engine=='playwright' and not web_url: blocked='web_url не настроен'
        if engine=='odata' and not odata_url: blocked='odata_url не настроен'
        engines.append({'index':index,'engine':engine,'mutating':bool(proposals[index].get('mutating')),
                        'blocked_reason':blocked})
    warnings.extend(mutation_conflicts(payload))
    strict_mode=bool(payload.get('strict_read_only'))
    if strict_mode:
        warnings.extend(strict_conflicts(payload))
        if strict_conflicts(payload):
            warnings.append('строгий режим включён: клики будут отклонены')
    try:
        key=login_guard.cooldown_key(s,project,payload) if project else None
        failures=login_guard.recent_login_failures(s,login_key=key)
        if failures >= login_guard.MAX_FAILURES:
            warnings.append(f'Вход в 1С приостановлен: {failures} неудачных попыток; cooldown target+principal')
    except Exception:
        pass
    allow_mode=bool(payload.get('allow_mutations'))
    return {'suite_id':sid,'status':row.status,'approved_indices':approved,'engines':engines,
            'settings':{'web_url_present':bool(web_url),'odata_url_present':bool(odata_url),
                        'username_present':bool(project and project.auth_username),
                        'has_password':bool(project and (project.auth_secret or project.auth_password))},
            'runtime':{'grace_seconds':WebClientRunner.LOGIN_GRACE_SECONDS,
                       'login_wait_seconds':WebClientRunner.LOGIN_WAIT_SECONDS,
                       'login_submit':WebClientRunner.LOGIN_SUBMIT,
                       'audit_max_issues':MAX_ISSUES_KEPT,
                       'login_cooldown_minutes':login_guard.COOLDOWN_MINUTES,
                       'login_max_failures':login_guard.MAX_FAILURES,
                       'allow_mutations_mode':allow_mode,
                       'strict_read_only_mode':strict_mode},
            'warnings':warnings,'would_run':blocker is None}


@router.post('/suites/{sid}/run-1c')
def run_1c(sid:str,request:Request,allow_mutations:bool=False,strict_read_only:bool=False,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    blocker=suite_preflight_blocker(s,row)
    if blocker:
        raise HTTPException(422,blocker)
    project=s.get(ProjectRow,row.project_id)
    if project and (project.odata_url or project.web_url):
        _profile_for_suite(row,project,s)
    from testpilot.ai.login_guard import cooldown_key
    login_key=cooldown_key(s, project, row.payload) if project else None
    ensure_login_allowed(s,row.payload,row.project_id,login_key=login_key)
    _require_run_mode(row.payload,allow_mutations,strict_read_only)
    _validate_run_assertions(row.payload or {})
    data=_enqueue(row,s,actor,request.headers.get('idempotency-key'),login_key,
                  allow_mutations=allow_mutations,strict_read_only=strict_read_only)
    if data.get('status') in ('queued','running'):
        return JSONResponse(status_code=202,content=data)
    return data


@router.get('/suites/{sid}/status')
def run_status(sid:str,actor=Depends(user),s=Depends(session,scope='function')):
    return _run_state(require(s.get(AISuiteRow,sid)))


@router.post('/suites/{sid}/cancel')
def cancel_run(sid:str,actor=Depends(expert),s=Depends(session,scope='function')):
    row=require(s.scalar(select(AISuiteRow).where(AISuiteRow.id==sid).with_for_update()))
    if row.status=='queued':
        row.status='cancelled';row.cancel_requested=True;row.finished_at=now()
        row.payload={**(row.payload or {}),'status':'cancelled','message':'Отменено до запуска'}
    elif row.status=='running' and (row.payload or {}).get('demo_claim'):
        raise HTTPException(409,'Демо-прогон на синтетическом стенде не прерывается: дождитесь завершения')
    elif row.status=='running':
        row.cancel_requested=True
        row.payload={**(row.payload or {}),'message':'Запрошена отмена: следующие шаги не выполняются'}
    elif row.status=='cancelled':
        return _run_state(row)
    else:
        raise HTTPException(409,'Прогон уже завершён; отмена не применима')
    audit(s,'intelligence',actor,'cancel_suite',sid,{'status':row.status})
    return _run_state(row)


@router.get('/suites/{sid}/report')
def run_report(sid:str,format:str='md',actor=Depends(user),s=Depends(session,scope='function'))->Response:
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
    if _is_demo_plan(source.payload):
        # Demo-план исполняется только синтетическим исполнителем учебного стенда:
        # rerun через очередь попал бы в execute_claim с пустым профилем реальной 1С.
        raise HTTPException(409,'Это учебный demo-план: повторный прогон доступен только через «Запустить на учебном стенде»; исполнитель реальной 1С для него не используется')
    from testpilot.ai.login_guard import cooldown_key as _ck
    _source_project=s.get(ProjectRow,source.project_id)
    ensure_login_allowed(s,source.payload,source.project_id,login_key=_ck(s,_source_project,source.payload) if _source_project else None)
    _require_run_mode(source.payload,allow_mutations,strict_read_only)
    new_id=uid()
    payload={**source.payload,'suite_id':new_id,'created_at':now(),'status':'approved','results':[]}
    for stale in ('counts','finished_at','executor','executor_1c_connected','message',
                  'step_results','progress_saved_at','business_success'):
        payload.pop(stale,None)
    row=AISuiteRow(id=new_id,package_id=source.package_id,project_id=source.project_id,status='approved',payload=payload)
    s.add(row);s.flush()
    audit(s,'intelligence',actor,'rerun_suite',new_id,{'source_suite':sid})
    project=s.get(ProjectRow,row.project_id)
    if project and (project.odata_url or project.web_url):
        _profile_for_suite(row,project,s)
    from testpilot.ai.login_guard import cooldown_key
    login_key=cooldown_key(s, project, row.payload) if project else None
    ensure_login_allowed(s,row.payload,row.project_id,login_key=login_key)
    _require_run_mode(row.payload,allow_mutations,strict_read_only)
    data=_enqueue(row,s,actor,None,login_key,allow_mutations=allow_mutations,strict_read_only=strict_read_only)
    return JSONResponse(status_code=202,content=data)


@router.delete('/suites/{sid}')
def delete_suite(sid:str,confirm:str|None=None,actor=Depends(expert),s=Depends(session,scope='function')):
    """Адресное удаление прогона одной транзакцией: evidence, тесты ai-<sid>-*, прогон ai-run-<sid> и их строки.

    Пакет и планы не трогаем: они могут использоваться другими suite. Без confirm=<sid> удаление не выполняется.
    """
    row=require(s.get(AISuiteRow,sid),'Сохранённый план не найден')
    if confirm!=sid:
        raise HTTPException(409,f'подтвердите удаление: передайте confirm={sid}')
    run_ids=[f'ai-run-{sid}']
    # Только тесты, созданные этим планом: id ai-<sid>-N. Тесты каталога (catalog_test_ids) могут быть общими.
    test_ids=sorted(s.scalars(select(TestRow.id).where(TestRow.id.like(f'ai-{sid}-%'))))
    deleted={}
    deleted['evidence']=s.execute(delete(EvidenceRow).where(EvidenceRow.suite_id==sid)).rowcount
    deleted['defects']=s.execute(delete(DefectRow).where(DefectRow.run_id.in_(run_ids))).rowcount
    if test_ids:
        deleted['defects']+=s.execute(delete(DefectRow).where(DefectRow.test_id.in_(test_ids))).rowcount
        deleted['results']=s.execute(delete(ResultRow).where(ResultRow.test_id.in_(test_ids))).rowcount
    else:
        deleted['results']=0
    deleted['results']+=s.execute(delete(ResultRow).where(ResultRow.run_id.in_(run_ids))).rowcount
    deleted['coverage']=s.execute(delete(CoverageRow).where(CoverageRow.test_id.in_(test_ids))).rowcount if test_ids else 0
    deleted['priorities']=s.execute(delete(PriorityRow).where(PriorityRow.test_id.in_(test_ids))).rowcount if test_ids else 0
    deleted['tests']=s.execute(delete(TestRow).where(TestRow.id.in_(test_ids))).rowcount if test_ids else 0
    deleted['runs']=s.execute(delete(RunRow).where(RunRow.id.in_(run_ids))).rowcount
    deleted['suites']=s.execute(delete(AISuiteRow).where(AISuiteRow.id==sid)).rowcount
    audit(s,'intelligence',actor,'delete_suite',sid,{'deleted':deleted,'package_id':row.package_id})
    return {'suite_id':sid,'deleted':deleted}


@router.get('/evidence/{eid}')
def evidence(eid:str,actor=Depends(user),s=Depends(session,scope='function')):
    e=require(s.get(EvidenceRow,eid));return Response(e.content,media_type='image/png',headers={'X-Content-Type-Options':'nosniff'})
