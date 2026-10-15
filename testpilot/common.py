import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from testpilot.db import engine

basic=HTTPBasic()
roles={'analyst': 'TP_ANALYST_PASSWORD','expert':'TP_EXPERT_PASSWORD','runner':'TP_RUNNER_PASSWORD'}
def user(c:HTTPBasicCredentials=Depends(basic)):
    expected=os.environ.get(roles.get(c.username,''),'')
    if not expected or not secrets.compare_digest(c.password.encode(),expected.encode()):
        raise HTTPException(401,'Неверные учётные данные',headers={'WWW-Authenticate':'Basic'})
    return c.username

def analyst(actor=Depends(user)):
    if actor not in ('analyst','expert'): raise HTTPException(403,'Требуется роль аналитика')
    return actor

def expert(actor=Depends(user)):
    if actor!='expert': raise HTTPException(403,'Требуется роль эксперта')
    return actor

def runner(actor=Depends(user)):
    if actor not in ('runner','expert'): raise HTTPException(403,'Требуется роль исполнителя')
    return actor

def app(title):
    a=FastAPI(title=title,version='1.0',dependencies=[])
    @a.middleware('http')
    async def limits(request:Request,call_next):
        if request.method in ('POST','PUT','PATCH'):
            file_upload=request.method=='POST' and request.url.path.endswith('/uploads') and '/projects/' in request.url.path
            limit=64*1024*1024 if file_upload else 8*1024*1024
            size=0
            chunks=[]
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit:
                    return JSONResponse({'error':{'code':413,'message':f'Файл превышает {limit//(1024*1024)} МиБ'}},status_code=413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
        return await call_next(request)
    @a.exception_handler(HTTPException)
    async def http_error(request,exc):
        return JSONResponse({'error':{'code':exc.status_code,'message':exc.detail}},status_code=exc.status_code,headers=exc.headers)
    @a.exception_handler(RequestValidationError)
    async def validation(request,exc):
        return JSONResponse({'error':{'code':422,'message':'Неверный формат данных','fields':[{'path':list(e['loc']),'message':e['msg']} for e in exc.errors()]}},status_code=422)
    @a.exception_handler(IntegrityError)
    async def integrity(request,exc):
        return JSONResponse({'error':{'code':409,'message':'Конфликт уникальности или ссылочной целостности'}},status_code=409)
    @a.get('/health')
    def health():
        with engine().connect() as c:
            c.execute(text('SELECT 1'))
            revision=c.execute(text('SELECT version_num FROM alembic_version')).scalar()
            if revision!='0004': raise HTTPException(503,'Миграции не обновлены до 0004')
        return {'status':'ok','executor_1c':'not_connected'}
    return a

def require(value,detail='Запись не найдена'):
    if value is None: raise HTTPException(404,detail)
    return value


async def upload_body(request:Request) -> bytes:
    return await request.body()
