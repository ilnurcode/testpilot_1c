"""Проекты и неизменяемая библиотека файлов. Бинарные форматы сохраняются без имитации разбора."""
import asyncio
import os
import hashlib
import io
import stat
from collections import Counter
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote
from zipfile import ZipFile, BadZipFile

from fastapi import APIRouter,Depends,HTTPException,Request,Query
from fastapi.responses import Response
from pydantic import Field,ValidationError
from sqlalchemy import select,func,delete,update,and_,or_,Integer
from sqlalchemy.orm import defer
from testpilot import secrets
from testpilot.common import analyst,expert,user,require,upload_body
from testpilot.target_policy import TargetDenied, check_target
from testpilot.contracts import Model,Text,Configuration,ImpactPackage,now,checksum
from testpilot.db import (session,ProjectRow,UploadRow,UploadAnalysisRow,VersionRow,AnalysisRow,AffectedRow,audit,uid,
                          AISuiteRow,EvidenceRow,PackageRow,PlanRow,AuditRow,
                          TestRow,CoverageRow,PriorityRow,ResultRow,DefectRow,RunRow)
from testpilot.impact.imports import store_version
from testpilot.impact.algorithms import analyze
from testpilot.integration.probe import probe, Profile, validated_url

router=APIRouter(prefix='/api/v1/projects',tags=['Проекты и загрузки'])
ALLOWED={'.json','.xml','.zip','.cfe','.cf','.epf','.erf','.dt'}
Kind=Literal['extension','processing','report','configuration','database']
MAX_FILE=64*1024*1024

class ProjectInput(Model):
    name: Text=Field(max_length=120)
    description: str=Field(default='',max_length=3000)

class AnalysisInput(Model):
    baseline_id: str|None='saved'
    max_depth: int=Field(default=10,ge=0,le=100)


def stored_password_state(s, p) -> dict:
    """Наличие секрета без его выдачи: только булевы признаки (ORM-поля, без запросов)."""
    encrypted = bool(p.auth_secret)
    legacy = bool(p.auth_password)
    return {'has_password': encrypted or legacy, 'encrypted': encrypted,
            'legacy_plaintext': legacy and not encrypted}


def load_connection_password(s, p) -> tuple[str | None, str | None]:
    """(пароль, предупреждение). Legacy-текст не используется; без ключа — отказ."""
    if p.auth_secret:
        return secrets.open_sealed(
            p.auth_secret,
            context=secrets.project_secret_context(p.id),
        ), None
    if p.auth_password:
        # Не fail-open: открытый текст не отдаётся для отправки даже с предупреждением.
        raise secrets.SecretsError(
            'Пароль подключения сохранён открытым текстом (legacy) и не используется. '
            'Сделайте backup и выполните scripts\\secrets_key.py --migrate-legacy --confirmed, '
            'либо повторно введите пароль владельцем.'
        )
    return None, None


def canonical_principal(username: str | None) -> str | None:
    """Нормализованный principal для идентичности цели: без учётных данных и регистра."""
    if not username:
        return None
    import unicodedata
    return unicodedata.normalize('NFC', username.strip())


def connection_revision(s, p) -> str:
    """Версия соединения: меняется при смене публикации, principal или секрета.

    Секрет не хешируется и не раскрывается: берутся только схема и время обновления.
    """
    material = {
        'odata_url': p.odata_url or None,
        'web_url': p.web_url or None,
        'principal': canonical_principal(p.auth_username),
        'has_password': bool(p.auth_secret or p.auth_password),
        'secret_scheme': p.auth_secret_scheme,
        'secret_updated_at': p.auth_secret_updated_at,
        'legacy_plaintext': bool(p.auth_password) and not p.auth_secret,
    }
    return checksum(material)[:16]


def require_schema_head(s=Depends(session)):
    """Понятный 503 при pending/ahead/недоступной БД до запросов к изменённым таблицам."""
    from testpilot.schema_state import describe, schema_error_message
    try:
        state = describe(s.connection())
    except Exception:
        raise HTTPException(503, 'База данных недоступна: проверьте, что PostgreSQL запущен. Схема не проверена.')
    if state['state'] != 'ok':
        raise HTTPException(503, schema_error_message(state))
    return state


def connection_public_identity(s, p) -> dict:
    """Публичная часть соединения для consent/approval: без секрета и password-hash."""
    state = stored_password_state(s, p)
    return {
        'odata_url': p.odata_url or None,
        'web_url': p.web_url or None,
        'auth_username': canonical_principal(p.auth_username),
        'has_password': bool(state['has_password']),
        'connection_revision': connection_revision(s, p),
    }


def target_identity(s, p) -> str:
    """Каноническая идентичность цели: публикация + principal + версия соединения.

    Не включает project_id, секрет и его хеш: две записи с одинаковым подключением
    дают одинаковую идентичность, а изменение настроек — новую.
    """
    public = connection_public_identity(s, p)
    return checksum({
        'publication': {'odata_url': public['odata_url'], 'web_url': public['web_url']},
        'principal': public['auth_username'],
        'connection_revision': public['connection_revision'],
    })


class RekeyRequired(RuntimeError):
    """Сохранённый секрет не расшифровывается текущим протектором: нужна явная перезапись."""


def store_connection_password(s, p, plaintext: str | None, *, clear: bool, rekey_confirm: bool = False) -> None:
    """Записывает секрет только в зашифрованном виде; открытый текст не сохраняется.

    Если у проекта уже есть auth_secret и он не расшифровывается текущим протектором,
    замена запрещена без явного rekey_confirm: иначе чужой/потерянный ключ молча
    уничтожил бы единственную копию пароля. Недоступный ключ остаётся 503 через seal().
    """
    if clear:
        p.auth_secret = None
        p.auth_secret_scheme = None
        p.auth_secret_updated_at = None
        p.auth_password = None
        return
    if p.auth_secret and not rekey_confirm and secrets.available():
        try:
            secrets.open_sealed(p.auth_secret, context=secrets.project_secret_context(p.id))
        except secrets.SecretsError as error:
            raise RekeyRequired(
                'Сохранённый пароль подключения не расшифровывается текущим ключом секретов. '
                'Перезапись без подтверждения запрещена: сделайте backup и повторите запрос '
                'с rekey_confirm=true, чтобы заменить секрет осознанно.'
            ) from error
    envelope, scheme = secrets.seal(
        plaintext or '',
        context=secrets.project_secret_context(p.id),
    )
    p.auth_secret = envelope
    p.auth_secret_scheme = scheme
    p.auth_secret_updated_at = now()
    # Legacy-поле не пишется; очистка старых значений — только явной проверенной миграцией.
    p.auth_password = None


class ConnectionInput(Model):
    odata_url: str|None=None
    web_url: str|None=None
    auth_username: str|None=None
    auth_password: str|None=None
    rekey_confirm: bool=False

def project_or_404(s,pid,lock=False):
    q=select(ProjectRow).where(ProjectRow.id==pid)
    if lock:q=q.with_for_update()
    return require(s.scalar(q),'Проект не найден')


def metadata_query():return select(UploadRow).options(defer(UploadRow.content))


def upload_or_404(s,pid,upload_id):
    return require(s.scalar(metadata_query().where(UploadRow.id==upload_id,UploadRow.project_id==pid)),'Загрузка в этом проекте не найдена')


UPLOAD_FIELDS=('id','project_id','number','filename','kind','component','label','size_bytes','checksum','status','message','version_id','baseline_id','created_by','created_at','summary')
RUN_COUNT_KEYS=('passed','failed','error','skipped')
# Терминальные статусы попытки прогона: отмена и прерывание — тоже выполненные попытки,
# иначе последний прогон молча показывает более старый completed и скрывает отмену/сбой.
# queued/running — активная попытка, а не терминальный прогон (отдельный active_run по
# контракту с Interface; до согласования в last_run не подмешиваются).
EXECUTED_SUITE_STATUSES=('completed','error','cancelled','interrupted')
ACTIVE_SUITE_STATUSES=('queued','running')

def upload_payload(values):
    payload={k:values[k] for k in UPLOAD_FIELDS}
    return payload | {'mode':'changes' if payload['baseline_id'] else 'full','has_audit':values.get('audit_result') is not None}


def upload_dict(r):
    return upload_payload({k:getattr(r,k) for k in UPLOAD_FIELDS} | {'audit_result':r.audit_result})


def last_run_payload(suite_id,status,payload,created_at):
    if not suite_id:return None
    counts=(payload or {}).get('counts') or {}
    return {'suite_id':suite_id,'status':status,'counts':{key:int(counts.get(key) or 0) for key in RUN_COUNT_KEYS},
            'finished_at':(payload or {}).get('finished_at'),'created_at':created_at}


def project_payload(p,*,upload_count,size_bytes,latest_upload,last_run=None,has_draft_plan=False,has_password=False):
    return {'id':p.id,'name':p.name,'description':p.description,'configuration_id':p.configuration_id,'created_by':p.created_by,'created_at':p.created_at,'upload_count':upload_count,'size_bytes':int(size_bytes),'latest_upload':latest_upload,'last_run':last_run,'has_draft_plan':bool(has_draft_plan),'connection_settings':{'odata_url':p.odata_url,'web_url':p.web_url,'auth_username':p.auth_username,'has_password':bool(has_password)}}


def project_has_draft(s,pid):
    return bool(s.scalar(select(func.count()).select_from(AISuiteRow).where(AISuiteRow.project_id==pid,AISuiteRow.status=='draft')))


def project_dict(s,p,last_run=None,has_password=False):
    count,size=s.execute(select(func.count(UploadRow.id),func.coalesce(func.sum(UploadRow.size_bytes),0)).where(UploadRow.project_id==p.id)).one()
    last=s.scalar(metadata_query().where(UploadRow.project_id==p.id).order_by(UploadRow.number.desc()).limit(1))
    return project_payload(p,upload_count=count,size_bytes=int(size),latest_upload=upload_dict(last) if last else None,last_run=last_run,
                           has_draft_plan=project_has_draft(s,p.id),has_password=has_password)


def project_last_run(s,pid):
    # Последний прогон — только выполненный: черновик плана не является прогоном.
    row=s.execute(select(AISuiteRow.id,AISuiteRow.status,AISuiteRow.payload,AISuiteRow.created_at)
                  .where(AISuiteRow.project_id==pid,AISuiteRow.status.in_(EXECUTED_SUITE_STATUSES))
                  .order_by(AISuiteRow.created_at.desc(),AISuiteRow.id.desc()).limit(1)).first()
    return last_run_payload(*row) if row else None


def filename_validated(name):
    if not name or len(name)>240 or name in ('.','..') or any(c in name for c in '/\\\r\n\x00') or any(ord(c)<32 for c in name):
        raise HTTPException(422,'Некорректное имя файла')
    suffix=PurePosixPath(name).suffix.lower()
    if suffix not in ALLOWED:raise HTTPException(422,'Поддерживаются JSON, ZIP, CFE, CF, EPF, ERF и DT')
    return suffix


def decode_configuration(raw):
    try:return Configuration.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(422,{'message':'Неверный нормализованный JSON конфигурации','fields':[{'path':list(x['loc']),'message':x['msg']} for x in e.errors()]})


def binary_extraction_hint(suffix):
    """Как получить исходники, если бинарный контейнер не дал модулей BSL."""
    if suffix in ('.cfe', '.cf'):
        return ('выполните в базе, где объект уже установлен, '
                '1cv8.exe DESIGNER /F "<база>" /DumpConfigToFiles "<каталог>"'
                + (' -Extension "<имя расширения>"' if suffix == '.cfe' else '')
                + ', затем загрузите ZIP этого каталога для аудита, анализа влияния и тестов.')
    return ('выполните 1cv8.exe DESIGNER /F "<база>" '
            f'/DumpExternalDataProcessorOrReportToFiles "<каталог>" "<файл{suffix}>" '
            'и загрузите ZIP этого каталога для аудита, анализа влияния и тестов.')


def inspect_file(raw,suffix,default_cfg_id='c1_extracted',default_version='1.0.0'):
    if suffix=='.json':
        if len(raw)>8*1024*1024:raise HTTPException(413,'Нормализованный JSON должен быть не больше 8 МиБ')
        return decode_configuration(raw),'Модель проверена и готова к анализу.','ready'
    if suffix=='.xml':
        try:
            from testpilot.impact.xml_adapter import XMLEdtAdapter
            return XMLEdtAdapter().parse(raw, configuration_id=default_cfg_id, version=default_version),'Модель XML/EDT 1С успешно разобрана.','ready'
        except Exception as e:
            raise HTTPException(422,f'Ошибка разбора XML выгрузки 1С: {e}')
    if suffix=='.zip':
        try:
            with ZipFile(io.BytesIO(raw)) as z:
                infos=z.infolist()
                if len(infos)>5000 or sum(i.file_size for i in infos)>128*1024*1024:
                    raise HTTPException(422,'Архив превышает лимит: 5000 записей или 128 МиБ после распаковки')
                names=set()
                for i in infos:
                    path=PurePosixPath(i.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in i.filename or ':' in i.filename or '\x00' in i.filename or stat.S_ISLNK(i.external_attr>>16) or i.flag_bits&1:
                        raise HTTPException(422,'Архив содержит небезопасный путь, ссылку или зашифрованную запись')
                    if i.filename in names:raise HTTPException(422,'Повторяющиеся пути в архиве')
                    names.add(i.filename)
                if 'configuration.json' in names:
                    info=z.getinfo('configuration.json')
                    if info.file_size>8*1024*1024:raise HTTPException(413,'configuration.json превышает 8 МиБ')
                    with z.open(info) as f:content=f.read(8*1024*1024+1)
                    if len(content)>8*1024*1024:raise HTTPException(413,'configuration.json превышает 8 МиБ')
                    return decode_configuration(content),'Нормализованная модель configuration.json извлечена из ZIP и проверена.','ready'
                if any(n.lower().endswith('.xml') for n in names):
                    try:
                        from testpilot.impact.xml_adapter import XMLEdtAdapter
                        return XMLEdtAdapter().parse(raw, configuration_id=default_cfg_id, version=default_version),'Выгрузка 1С XML/EDT из архива успешно разобрана.','ready'
                    except Exception:
                        pass
                return None,'Архив сохранён. Для анализа исходников нужен адаптер их формата; configuration.json в корне отсутствует.','requires_adapter'
        except (BadZipFile,RuntimeError,NotImplementedError):raise HTTPException(422,'Архив повреждён или использует неподдерживаемое сжатие')
    if suffix in ('.cfe', '.epf', '.erf', '.cf'):
        report = None
        model = None
        try:
            from testpilot.impact.binary_adapter import Binary1CAdapter
            model, report = Binary1CAdapter().parse_with_report(raw, filename=f"upload{suffix}", configuration_id=default_cfg_id, version=default_version)
        except (ValueError, OSError):
            report = None
        if report is not None and report.has_bsl and model and model.objects:
            return model, (f'Бинарный контейнер {suffix.upper()[1:]} прочитан и разобран: модулей BSL — {len(report.bsl_modules)}, '
                           f'объектов — {len(model.objects)}, связей — {len(model.edges)}.'), 'ready'
        if report is not None:
            # Контейнер прочитан, но исходников в нём нет: версия не создаётся, чтобы не выдавать
            # метаданные контейнера за разобранные модули и связи.
            return None, (f'Бинарный контейнер {suffix.upper()[1:]} прочитан: найдено внутренних потоков (метаданных) — {report.streams}, '
                          'модулей BSL не извлечено. Объекты и связи из кода не построены, версия не создана. '
                          'Метаданные без модулей не дают области влияния; чтобы получить исходники, '
                          + binary_extraction_hint(suffix)), 'metadata_only'
    if suffix in ('.cfe', '.cf'):
        return None,('Двоичный файл сохранён. Модули BSL внутри него автоматически не разбираются: '  # noqa: E501
                     + binary_extraction_hint(suffix)), 'requires_adapter'
    if suffix in ('.epf', '.erf'):
        return None,('Двоичный файл сохранён. Модули BSL внутри него автоматически не разбираются: '  # noqa: E501
                     + binary_extraction_hint(suffix)), 'requires_adapter'
    return None,'Файл сохранён. Разбор этого формата требует проверенного адаптера или агента 1С; анализ ещё не выполнялся.','requires_adapter'


@router.post('')
def create_project(q:ProjectInput,actor=Depends(analyst),s=Depends(session,scope='function')):
    if not q.name.strip():raise HTTPException(422,'Укажите название проекта')
    p=ProjectRow(name=q.name.strip(),description=q.description.strip(),created_by=actor)
    s.add(p);s.flush();audit(s,'impact',actor,'create_project',p.id)
    return project_dict(s,p)


@router.get('')
def list_projects(q:str|None=None,only_with_issues:bool=False,limit:int=50,offset:int=0,
                  actor=Depends(user),s=Depends(session,scope='function')):
    """Страница списка проектов: q — подстрока имени, only_with_issues — последний прогон с расхождениями/ошибками.

    На страницу уходит один запрос за items и один дешёвый count за total (два SQL, без N+1).
    """
    if limit<1 or limit>200:raise HTTPException(400,'limit должен быть от 1 до 200')
    if offset<0:raise HTTPException(400,'offset не может быть отрицательным')
    stats=select(UploadRow.project_id.label('project_id'),func.count(UploadRow.id).label('upload_count'),
                 func.coalesce(func.sum(UploadRow.size_bytes),0).label('size_bytes')).group_by(UploadRow.project_id).subquery()
    latest_upload=select(*[getattr(UploadRow,f) for f in UPLOAD_FIELDS],
                         UploadRow.audit_result.label('audit_result'),
                         func.row_number().over(partition_by=UploadRow.project_id,order_by=UploadRow.number.desc()).label('rn')).subquery()
    latest_suite=select(AISuiteRow.project_id.label('project_id'),AISuiteRow.id.label('suite_id'),AISuiteRow.status.label('status'),
                        AISuiteRow.payload.label('payload'),AISuiteRow.created_at.label('created_at'),
                        func.row_number().over(partition_by=AISuiteRow.project_id,
                                               order_by=(AISuiteRow.created_at.desc(),AISuiteRow.id.desc())).label('rn')
                        ).where(AISuiteRow.status.in_(EXECUTED_SUITE_STATUSES)).subquery()
    draft_flags=select(AISuiteRow.project_id.label('project_id'),
                       func.count(AISuiteRow.id).label('draft_count')).where(AISuiteRow.status=='draft').group_by(AISuiteRow.project_id).subquery()
    upload_columns=[latest_upload.c[f].label(f'up_{f}') for f in UPLOAD_FIELDS]+[latest_upload.c.audit_result.label('up_audit_result')]
    suite_columns=[latest_suite.c.suite_id.label('run_suite_id'),latest_suite.c.status.label('run_status'),
                   latest_suite.c.payload.label('run_payload'),latest_suite.c.created_at.label('run_created_at')]
    stmt=(select(ProjectRow,stats.c.upload_count.label('upload_count'),stats.c.size_bytes.label('size_bytes'),
                 draft_flags.c.draft_count.label('draft_count'),
                 *upload_columns,*suite_columns)
          .outerjoin(stats,stats.c.project_id==ProjectRow.id)
          .outerjoin(draft_flags,draft_flags.c.project_id==ProjectRow.id)
          .outerjoin(latest_upload,and_(latest_upload.c.project_id==ProjectRow.id,latest_upload.c.rn==1))
          .outerjoin(latest_suite,and_(latest_suite.c.project_id==ProjectRow.id,latest_suite.c.rn==1)))
    conditions=[]
    if q and q.strip():conditions.append(ProjectRow.name.ilike(f'%{q.strip()}%'))
    if only_with_issues:
        def payload_count(key):
            return func.coalesce(func.cast(latest_suite.c.payload.op('->')('counts').op('->>')(key),Integer),0)
        conditions.append(and_(latest_suite.c.rn==1,or_(payload_count('failed')>0,payload_count('error')>0)))
    if conditions:stmt=stmt.where(*conditions)
    total=int(s.scalar(select(func.count()).select_from(stmt.with_only_columns(ProjectRow.id).subquery())) or 0)
    page=stmt.order_by(ProjectRow.created_at.desc(),ProjectRow.id.desc()).limit(limit).offset(offset)
    rows=list(s.execute(page).all())
    projects=[]
    for row in rows:
        values=row._mapping
        project=values[ProjectRow]
        upload=None
        if values['up_id']:
            upload=upload_payload({f:values[f'up_{f}'] for f in UPLOAD_FIELDS} | {'audit_result':values['up_audit_result']})
        projects.append(project_payload(project,upload_count=int(values['upload_count'] or 0),size_bytes=int(values['size_bytes'] or 0),
                                        latest_upload=upload,
                                        last_run=last_run_payload(values['run_suite_id'],values['run_status'],
                                                                  values['run_payload'],values['run_created_at']),
                                        has_draft_plan=bool(values['draft_count']),
                                        has_password=bool(project.auth_secret or project.auth_password)))
    return {'items':projects,'total':total,'limit':limit,'offset':offset}


@router.get('/{pid}')
def get_project(pid:str,actor=Depends(user),s=Depends(session,scope='function')):
    p=project_or_404(s,pid)
    uploads=list(s.scalars(metadata_query().where(UploadRow.project_id==pid).order_by(UploadRow.number.desc())))
    runs=list(s.scalars(select(UploadAnalysisRow).where(UploadAnalysisRow.project_id==pid).order_by(UploadAnalysisRow.created_at.desc())))
    return {**project_dict(s,p,last_run=project_last_run(s,pid),
                           has_password=bool(p.auth_secret or p.auth_password)),'uploads':[upload_dict(r) for r in uploads],
            'analyses':[{'analysis_id':r.analysis_id,'upload_id':r.upload_id,'baseline_id':r.baseline_id,'mode':r.mode,'created_at':r.created_at,'affected_count':len(s.get(AnalysisRow,r.analysis_id).package['affected_objects'])} for r in runs]}


@router.post('/{pid}/uploads')
def upload_file(pid:str,raw:bytes=Depends(upload_body),filename:str=Query(min_length=1,max_length=240),kind:Kind='extension',component:str=Query(default='Основная разработка',min_length=1,max_length=120),label:str|None=Query(default=None,max_length=120),baseline_id:str='auto',actor=Depends(analyst),s=Depends(session,scope='function')):
    suffix=filename_validated(filename)
    component=component.strip()
    if not component or any(ord(c)<32 for c in component):raise HTTPException(422,'Укажите корректное название компонента')
    if label and any(ord(c)<32 for c in label):raise HTTPException(422,'Некорректное название версии')
    if not raw:raise HTTPException(422,'Пустой файл не сохраняется')
    if len(raw)>MAX_FILE:raise HTTPException(413,'Файл превышает 64 МиБ')
    digest=hashlib.sha256(raw).hexdigest()
    # Блокировка проекта сериализует номер и выбор предыдущей загрузки.
    p=project_or_404(s,pid,lock=True)
    old=s.scalar(metadata_query().where(UploadRow.project_id==pid,UploadRow.component==component,UploadRow.kind==kind,UploadRow.checksum==digest))
    if old:return {**upload_dict(old),'idempotent':True}
    cfg_hint=p.configuration_id or f'proj-{pid[:8]}'
    ver_hint=(label or '').strip() or f'1.0.{p.next_number+1}'
    model,message,file_status=inspect_file(raw,suffix,default_cfg_id=cfg_hint,default_version=ver_hint)
    previous=None
    if baseline_id=='auto':
        previous=s.scalar(metadata_query().where(UploadRow.project_id==pid,UploadRow.kind==kind,UploadRow.component==component).order_by(UploadRow.number.desc()).limit(1))
    elif baseline_id!='none':
        previous=upload_or_404(s,pid,baseline_id)
        if previous.kind!=kind or previous.component!=component:raise HTTPException(422,'Базовая версия должна относиться к тому же компоненту и типу')
    version_id=None;summary={}
    if model:
        if p.configuration_id and p.configuration_id!=model.configuration_id:raise HTTPException(409,'Конфигурация JSON не совпадает с конфигурацией проекта')
        elsewhere=s.scalar(select(ProjectRow).where(ProjectRow.configuration_id==model.configuration_id,ProjectRow.id!=pid))
        if elsewhere:raise HTTPException(409,'Эта конфигурация уже связана с другим проектом')
        p.configuration_id=model.configuration_id
        stored=store_version(s,model,raw,actor);version_id=stored['id']
        summary={'configuration_id':model.configuration_id,'version':model.version,'object_count':len(model.objects),'edge_count':len(model.edges),'object_types':dict(Counter(o.type for o in model.objects)),'synthetic':model.synthetic}
    p.next_number+=1
    title=(label or '').strip() or f'Загрузка {p.next_number}'
    conflict=s.scalar(metadata_query().where(UploadRow.project_id==pid,UploadRow.kind==kind,UploadRow.component==component,UploadRow.label==title))
    if conflict:raise HTTPException(409,'Название версии уже используется. Укажите другое название; предыдущий файл не изменён.')
    row=UploadRow(id=uid(),project_id=pid,number=p.next_number,filename=filename,kind=kind,component=component,label=title,size_bytes=len(raw),checksum=digest,content=raw,status='ready' if model else file_status,message=message,version_id=version_id,baseline_id=previous.id if previous else None,created_by=actor,summary=summary)
    s.add(row);s.flush();audit(s,'impact',actor,'upload_file',row.id,{'project_id':pid,'filename':filename,'checksum':digest,'baseline_id':row.baseline_id})
    return {**upload_dict(row),'idempotent':False}


@router.get('/{pid}/uploads/{upload_id}/download')
def download(pid:str,upload_id:str,actor=Depends(user),s=Depends(session,scope='function')):
    r=upload_or_404(s,pid,upload_id)
    return Response(r.content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(r.filename,safe=''),'X-Content-Type-Options':'nosniff','X-Checksum-Sha256':r.checksum})


@router.post('/{pid}/uploads/{upload_id}/analyze')
async def analyze_upload(pid:str,upload_id:str,q:AnalysisInput,actor=Depends(analyst),s=Depends(session,scope='function')):
    project_or_404(s,pid);target_upload=upload_or_404(s,pid,upload_id)
    if not target_upload.version_id:raise HTTPException(409,{'message':'Файл сохранён, но ещё не разобран. Нужен адаптер формата или нормализованный JSON.','status':target_upload.status})
    target=s.get(VersionRow,target_upload.version_id)
    baseline_id=target_upload.baseline_id if q.baseline_id=='saved' else q.baseline_id
    previous=upload_or_404(s,pid,baseline_id) if baseline_id else None
    if previous:
        if previous.number>=target_upload.number or previous.kind!=target_upload.kind or previous.component!=target_upload.component:
            raise HTTPException(422,'Выберите более раннюю загрузку того же компонента и типа')
        if not previous.version_id:raise HTTPException(409,'Базовый файл ещё не разобран; выберите разобранную версию либо полный анализ')
        base=s.get(VersionRow,previous.version_id);base_payload=base.payload;mode='changes'
    else:
        base=None;mode='full'
        base_payload={**target.payload,'version':'__no_previous_version__','objects':[],'edges':[]}
    parameters={'configuration_id':target.configuration_id,'base_version':base_payload['version'],'target_version':target.version,'max_depth':q.max_depth,'edge_types':['calls','uses','belongs_to','subscribes_to'],'direction':'impact','mode':mode,'project_id':pid,'upload_id':upload_id,'baseline_upload_id':previous.id if previous else None}
    changes,package=await asyncio.to_thread(analyze,base_payload,target.payload,parameters)
    package['warnings'].append('Анализ ограничен загруженной моделью. Связи с типовой конфигурацией доступны только если они представлены в модели.')
    if mode=='full':package['warnings'].append('Предыдущая версия не выбрана: вся модель включена в первичный анализ. Это не сравнение с реальной прошлой версией.')
    from testpilot.contracts import checksum
    package['checksum']=checksum({k:v for k,v in package.items() if k!='checksum'})
    ImpactPackage.model_validate(package)
    aid=package['analysis_id']
    s.add(AnalysisRow(id=aid,base_id=base.id if base else None,target_id=target.id,parameters=parameters,comparison=changes,package=package));s.flush()
    paths={oid:[] for oid in package['affected_objects']}
    for path in package['dependency_paths']:paths[path['affected_object']].append(path)
    for oid in package['affected_objects']:s.add(AffectedRow(analysis_id=aid,object_id=oid,paths=paths[oid]))
    s.add(UploadAnalysisRow(analysis_id=aid,project_id=pid,upload_id=upload_id,baseline_id=previous.id if previous else None,mode=mode))
    audit(s,'impact',actor,'analyze_upload',aid,{'project_id':pid,'mode':mode})
    names={o['object_id']:o['full_name'] for d in (base_payload,target.payload) for o in d['objects']}
    return {'mode':mode,'analysis_id':aid,'changes':changes,'package':package,'parameters':parameters,'object_names':names}


@router.put('/{pid}/connection')
def update_connection(pid:str,q:ConnectionInput,actor=Depends(analyst),s=Depends(session,scope='function'),_schema=Depends(require_schema_head)):
    p=project_or_404(s,pid,lock=True)
    if q.odata_url and q.odata_url.strip():
        try:
            value=validated_url(q.odata_url.strip())
            p.odata_url=check_target(value,purpose='odata_url')
        except TargetDenied as e:raise HTTPException(422,f'odata_url отклонён до сохранения: {e}')
        except ValueError as e:raise HTTPException(422,f'Некорректный odata_url: {e}')
    else:p.odata_url=None
    if q.web_url and q.web_url.strip():
        try:
            value=validated_url(q.web_url.strip())
            p.web_url=check_target(value,purpose='web_url')
        except TargetDenied as e:raise HTTPException(422,f'web_url отклонён до сохранения: {e}')
        except ValueError as e:raise HTTPException(422,f'Некорректный web_url: {e}')
    else:p.web_url=None
    p.auth_username=q.auth_username.strip() if q.auth_username and q.auth_username.strip() else None
    if q.auth_password is not None:
        try:
            store_connection_password(s,p,q.auth_password,clear=bool(not q.auth_password),
                                      rekey_confirm=bool(q.rekey_confirm))
        except RekeyRequired as e:
            raise HTTPException(409,str(e)) from e
        except secrets.SecretsError as e:
            raise HTTPException(503,f'Секрет подключения недоступен: {e}') from e
    if not p.auth_username:
        store_connection_password(s,p,None,clear=True)
    s.flush()
    state=stored_password_state(s,p)
    audit(s,'impact',actor,'update_connection',p.id,{'has_odata':bool(p.odata_url),'has_web':bool(p.web_url),'has_username':bool(p.auth_username),'encrypted_secret':state['encrypted'],'rekey_confirmed':bool(q.rekey_confirm)})
    return {'ok':True,'connection_settings':{'odata_url':p.odata_url,'web_url':p.web_url,'auth_username':p.auth_username,'has_password':state['has_password'],'password_encrypted':state['encrypted']}}


def _resolve_probe_url(url: str | None) -> str | None:
    if not url:
        return url
    from pathlib import Path
    if Path('/.dockerenv').exists():
        return url.replace('://localhost', '://host.docker.internal').replace('://127.0.0.1', '://host.docker.internal')
    return url

@router.post('/{pid}/connection/probe')
async def probe_connection(pid:str,actor=Depends(analyst),s=Depends(session,scope='function'),_schema=Depends(require_schema_head)):
    p=project_or_404(s,pid)
    declared_odata=p.odata_url or None
    declared_web=p.web_url or None
    if not declared_odata and not declared_web:
        raise HTTPException(422,'Задайте odata_url или web_url подключения проекта')
    try:
        if declared_odata:
            check_target(validated_url(declared_odata),purpose='odata_url')
        if declared_web:
            check_target(validated_url(declared_web),purpose='web_url')
    except TargetDenied as e:
        raise HTTPException(422,f'Диагностика не выполнялась: {e}')
    except ValueError as e:
        raise HTTPException(422,f'Некорректный URL подключения: {e}')
    odata_url=_resolve_probe_url(declared_odata)
    web_url=_resolve_probe_url(declared_web)
    username=p.auth_username or None
    password=None
    password_warnings=[]
    if username:
        try:
            password, warning = load_connection_password(s,p)
        except secrets.SecretsError as e:
            raise HTTPException(503,f'Секрет подключения недоступен: {e}')
        if warning: password_warnings.append(warning)
    authorization=None
    ca_file=os.environ.get('TP_1C_CA_FILE') or None
    try:
        profile=Profile(odata_url=odata_url,web_url=web_url,username=username,password=password,authorization=authorization,ca_file=ca_file)
    except ValueError as e:
        raise HTTPException(422,f'Ошибка конфигурации подключения: {e}')
    try:
        result = await asyncio.to_thread(probe,profile)
        result.setdefault('warnings',[]).extend(password_warnings)
        return result
    except Exception as e:
        return {'odata':{'status':'connection_error','message':str(e)},'web':{'status':'connection_error','message':str(e)},'code_changes_detected':False,'tests_executed':0,'warnings':password_warnings+[f'Ошибка диагностики: {e}']}


@router.get('/{pid}/connection/enable-odata-script')
def enable_odata_script(pid:str,actor=Depends(user),s=Depends(session,scope='function')):
    p=project_or_404(s,pid)
    script=(
        "// =============================================================================\n"
        f"// TestPilot 1C: Скрипт включения состава стандартного интерфейса OData\n"
        f"// Проект: {p.name} (ID: {p.id})\n"
        "// Инструкция по выполнению:\n"
        "// 1. Откройте 1С:Предприятие в режиме предприятия под пользователем с полными правами.\n"
        "// 2. Откройте внешнюю обработку или консоль кода.\n"
        "// 3. Выполните приведенный ниже BSL-код.\n"
        "// 4. После выполнения состав сущностей станет доступен через REST OData.\n"
        "// =============================================================================\n\n"
        "Процедура ВключитьСоставOData()\n"
        "    СоставOData = Новый Массив;\n\n"
        "    // Справочники\n"
        "    Для Каждого Мета Из Метаданные.Справочники Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Документы\n"
        "    Для Каждого Мета Из Метаданные.Документы Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Регистры сведений\n"
        "    Для Каждого Мета Из Метаданные.РегистрыСведений Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Регистры накопления\n"
        "    Для Каждого Мета Из Метаданные.РегистрыНакопления Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Регистры бухгалтерии\n"
        "    Для Каждого Мета Из Метаданные.РегистрыБухгалтерии Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Регистры расчета\n"
        "    Для Каждого Мета Из Метаданные.РегистрыРасчета Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Планы счетов\n"
        "    Для Каждого Мета Из Метаданные.ПланыСчетов Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Планы видов характеристик\n"
        "    Для Каждого Мета Из Метаданные.ПланыВидовХарактеристик Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Бизнес-процессы\n"
        "    Для Каждого Мета Из Метаданные.БизнесПроцессы Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Задачи\n"
        "    Для Каждого Мета Из Метаданные.Задачи Цикл\n"
        "        СоставOData.Добавить(Мета.ПолноеИмя());\n"
        "    КонецЦикла;\n\n"
        "    // Установка состава стандартного интерфейса OData\n"
        "    УстановитьСоставСтандартногоИнтерфейсаOData(СоставOData);\n\n"
        '    Сообщить("Состав стандартного интерфейса OData успешно обновлен. Опубликовано объектов: " + Строка(СоставOData.Количество()));\n'
        "КонецПроцедуры\n\n"
        "ВключитьСоставOData();\n"
    )
    return Response(
        content=script,
        media_type='text/plain; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename="enable_odata.bsl"',
            'X-Content-Type-Options': 'nosniff',
        }
    )

@router.delete('/{pid}')
def delete_project(pid:str,confirm:str|None=None,actor=Depends(expert),s=Depends(session,scope='function')):
    """Удаляет проект и все связанные записи одной транзакцией; без confirm=<pid> удаление не выполняется.

    Каскад: impact_uploads, impact_upload_analyses, impact_analyses + impact_affected, ti_ai_suites +
    ti_ai_evidence, тесты утверждённых планов (id ai-<suite>-<n>) с ti_coverage/ti_results/ti_defects/
    ti_priorities, прогоны ai-run-<suite> (ti_runs), пакеты без чужих ссылок и записи audit по этим id.
    Остаются осознанно: пакет, на который ссылаются чужие suite или ti_plans; тест, уже взятый в план
    другого проекта (ti_priorities); ti_plans / ti_execution_jobs / ti_ml_models и справочники
    конфигурации (версии, объекты, связи) — у них нет ссылки на проект; журнал code_audit — тоже.
    """
    project_or_404(s,pid,lock=True)
    if confirm!=pid:
        raise HTTPException(409,f'подтвердите удаление: передайте confirm={pid}')
    deleted={}
    suites=[(row.id,row.payload or {}) for row in s.scalars(select(AISuiteRow).where(AISuiteRow.project_id==pid))]
    suite_ids=[sid for sid,_ in suites]
    package_ids=sorted({row for (row,) in s.execute(select(AISuiteRow.package_id).where(AISuiteRow.project_id==pid))})
    analysis_ids=[row for (row,) in s.execute(select(UploadAnalysisRow.analysis_id).where(UploadAnalysisRow.project_id==pid))]
    upload_ids=[row for (row,) in s.execute(select(UploadRow.id).where(UploadRow.project_id==pid))]
    run_ids=[f'ai-run-{sid}' for sid in suite_ids]
    test_candidates=set()
    for sid,payload in suites:
        for tid in (payload.get('catalog_test_ids') or {}).values():
            test_candidates.add(tid)
    for tid in [row for (row,) in s.execute(select(TestRow.id))]:
        if any(tid.startswith(f'ai-{sid}-') for sid in suite_ids):
            test_candidates.add(tid)
    shared_tests={row for (row,) in s.execute(select(PriorityRow.test_id).where(PriorityRow.test_id.in_(test_candidates)))} if test_candidates else set()
    test_ids=sorted(test_candidates-shared_tests)

    deleted['evidence']=s.execute(delete(EvidenceRow).where(EvidenceRow.suite_id.in_(suite_ids))).rowcount if suite_ids else 0
    deleted['suites']=s.execute(delete(AISuiteRow).where(AISuiteRow.project_id==pid)).rowcount
    removable=[]
    if package_ids:
        busy_suites={row for (row,) in s.execute(select(AISuiteRow.package_id).where(AISuiteRow.package_id.in_(package_ids)))}
        busy_plans={row for (row,) in s.execute(select(PlanRow.package_id).where(PlanRow.package_id.in_(package_ids)))}
        removable=[pkg for pkg in package_ids if pkg not in busy_suites and pkg not in busy_plans]
    deleted['packages']=s.execute(delete(PackageRow).where(PackageRow.id.in_(removable))).rowcount if removable else 0

    def count(statement):
        return s.execute(statement).rowcount

    deleted['defects']=count(delete(DefectRow).where(DefectRow.test_id.in_(test_ids))) if test_ids else 0
    deleted['defects']+=count(delete(DefectRow).where(DefectRow.run_id.in_(run_ids))) if run_ids else 0
    deleted['results']=count(delete(ResultRow).where(ResultRow.test_id.in_(test_ids))) if test_ids else 0
    deleted['results']+=count(delete(ResultRow).where(ResultRow.run_id.in_(run_ids))) if run_ids else 0
    deleted['coverage']=count(delete(CoverageRow).where(CoverageRow.test_id.in_(test_ids))) if test_ids else 0
    deleted['priorities']=count(delete(PriorityRow).where(PriorityRow.test_id.in_(test_ids))) if test_ids else 0
    deleted['tests']=count(delete(TestRow).where(TestRow.id.in_(test_ids))) if test_ids else 0
    deleted['runs']=count(delete(RunRow).where(RunRow.id.in_(run_ids))) if run_ids else 0

    deleted['upload_analyses']=s.execute(delete(UploadAnalysisRow).where(UploadAnalysisRow.project_id==pid)).rowcount
    if analysis_ids:
        deleted['affected']=s.execute(delete(AffectedRow).where(AffectedRow.analysis_id.in_(analysis_ids))).rowcount
        deleted['analyses']=s.execute(delete(AnalysisRow).where(AnalysisRow.id.in_(analysis_ids))).rowcount
    else:
        deleted['affected']=0;deleted['analyses']=0
    s.execute(update(UploadRow).where(UploadRow.project_id==pid).values(baseline_id=None))
    deleted['uploads']=s.execute(delete(UploadRow).where(UploadRow.project_id==pid)).rowcount

    entity_ids=[pid,*upload_ids,*analysis_ids,*suite_ids,*test_ids,*run_ids,*removable]
    deleted['audit']=s.execute(delete(AuditRow).where(AuditRow.entity_id.in_(entity_ids))).rowcount
    s.execute(delete(ProjectRow).where(ProjectRow.id==pid))
    deleted['projects']=1
    audit(s,'impact',actor,'delete_project',pid,{'deleted':deleted,'shared_tests':len(shared_tests)})
    return {'project_id':pid,'deleted':deleted}
