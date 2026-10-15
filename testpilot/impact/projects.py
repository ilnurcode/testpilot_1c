"""Проекты и неизменяемая библиотека файлов. Бинарные форматы сохраняются без имитации разбора."""
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
from sqlalchemy import select,func
from sqlalchemy.orm import defer
from testpilot.common import analyst,user,require,upload_body
from testpilot.contracts import Model,Text,Configuration,ImpactPackage
from testpilot.db import (session,ProjectRow,UploadRow,UploadAnalysisRow,VersionRow,AnalysisRow,AffectedRow,audit,uid)
from testpilot.impact.imports import store_version
from testpilot.impact.algorithms import analyze

router=APIRouter(prefix='/api/v1/projects',tags=['Проекты и загрузки'])
ALLOWED={'.json','.zip','.cfe','.cf','.epf','.erf','.dt'}
Kind=Literal['extension','processing','report','configuration','database']
MAX_FILE=64*1024*1024

class ProjectInput(Model):
    name: Text=Field(max_length=120)
    description: str=Field(default='',max_length=3000)

class AnalysisInput(Model):
    baseline_id: str|None='saved'
    max_depth: int=Field(default=10,ge=0,le=100)


def project_or_404(s,pid,lock=False):
    q=select(ProjectRow).where(ProjectRow.id==pid)
    if lock:q=q.with_for_update()
    return require(s.scalar(q),'Проект не найден')


def metadata_query():return select(UploadRow).options(defer(UploadRow.content))


def upload_or_404(s,pid,upload_id):
    return require(s.scalar(metadata_query().where(UploadRow.id==upload_id,UploadRow.project_id==pid)),'Загрузка в этом проекте не найдена')


def upload_dict(r):
    return {k:getattr(r,k) for k in ('id','project_id','number','filename','kind','component','label','size_bytes','checksum','status','message','version_id','baseline_id','created_by','created_at','summary')} | {'mode':'changes' if r.baseline_id else 'full'}


def project_dict(s,p):
    count,size=s.execute(select(func.count(UploadRow.id),func.coalesce(func.sum(UploadRow.size_bytes),0)).where(UploadRow.project_id==p.id)).one()
    last=s.scalar(metadata_query().where(UploadRow.project_id==p.id).order_by(UploadRow.number.desc()).limit(1))
    return {'id':p.id,'name':p.name,'description':p.description,'configuration_id':p.configuration_id,'created_by':p.created_by,'created_at':p.created_at,'upload_count':count,'size_bytes':int(size),'latest_upload':upload_dict(last) if last else None}


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


def inspect_file(raw,suffix):
    if suffix=='.json':
        if len(raw)>8*1024*1024:raise HTTPException(413,'Нормализованный JSON должен быть не больше 8 МиБ')
        return decode_configuration(raw),'Модель проверена и готова к анализу.'
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
                    return decode_configuration(content),'Нормализованная модель configuration.json извлечена из ZIP и проверена.'
                return None,'Архив сохранён. Для анализа исходников нужен адаптер их формата; configuration.json в корне отсутствует.'
        except (BadZipFile,RuntimeError,NotImplementedError):raise HTTPException(422,'Архив повреждён или использует неподдерживаемое сжатие')
    return None,'Файл сохранён. Разбор этого формата требует проверенного адаптера или агента 1С; анализ ещё не выполнялся.'


@router.post('')
def create_project(q:ProjectInput,actor=Depends(analyst),s=Depends(session,scope='function')):
    if not q.name.strip():raise HTTPException(422,'Укажите название проекта')
    p=ProjectRow(name=q.name.strip(),description=q.description.strip(),created_by=actor)
    s.add(p);s.flush();audit(s,'impact',actor,'create_project',p.id)
    return project_dict(s,p)


@router.get('')
def list_projects(actor=Depends(user),s=Depends(session,scope='function')):
    return [project_dict(s,p) for p in s.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc()))]


@router.get('/{pid}')
def get_project(pid:str,actor=Depends(user),s=Depends(session,scope='function')):
    p=project_or_404(s,pid)
    uploads=list(s.scalars(metadata_query().where(UploadRow.project_id==pid).order_by(UploadRow.number.desc())))
    runs=list(s.scalars(select(UploadAnalysisRow).where(UploadAnalysisRow.project_id==pid).order_by(UploadAnalysisRow.created_at.desc())))
    return {**project_dict(s,p),'uploads':[upload_dict(r) for r in uploads],
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
    model,message=inspect_file(raw,suffix)
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
    row=UploadRow(id=uid(),project_id=pid,number=p.next_number,filename=filename,kind=kind,component=component,label=title,size_bytes=len(raw),checksum=digest,content=raw,status='ready' if model else 'requires_adapter',message=message,version_id=version_id,baseline_id=previous.id if previous else None,created_by=actor,summary=summary)
    s.add(row);s.flush();audit(s,'impact',actor,'upload_file',row.id,{'project_id':pid,'filename':filename,'checksum':digest,'baseline_id':row.baseline_id})
    return {**upload_dict(row),'idempotent':False}


@router.get('/{pid}/uploads/{upload_id}/download')
def download(pid:str,upload_id:str,actor=Depends(user),s=Depends(session,scope='function')):
    r=upload_or_404(s,pid,upload_id)
    return Response(r.content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(r.filename,safe=''),'X-Content-Type-Options':'nosniff','X-Checksum-Sha256':r.checksum})


@router.post('/{pid}/uploads/{upload_id}/analyze')
def analyze_upload(pid:str,upload_id:str,q:AnalysisInput,actor=Depends(analyst),s=Depends(session,scope='function')):
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
    changes,package=analyze(base_payload,target.payload,parameters)
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
