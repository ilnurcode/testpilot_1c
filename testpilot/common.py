import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from testpilot.db import engine
from testpilot.schema_state import describe as describe_schema, schema_error_message

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

def openapi_is_public():
    """Схема API открыта только при явном TP_OPENAPI_PUBLIC=1 (локальная разработка)."""
    return (os.environ.get('TP_OPENAPI_PUBLIC') or '').strip().lower() in ('1','true','yes','on')


def app(title, *, lifespan=None):
    from testpilot.config import validate_startup_urls
    validate_startup_urls()
    public_docs=openapi_is_public()
    a=FastAPI(title=title,version='1.0',dependencies=[],lifespan=lifespan,
              docs_url='/docs' if public_docs else None,
              redoc_url='/redoc' if public_docs else None,
              openapi_url='/openapi.json' if public_docs else None)
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
    if not public_docs:
        # По умолчанию схема и UI документации закрыты Basic Auth: TP_OPENAPI_PUBLIC=1 открывает их локально.
        from fastapi.openapi.docs import get_redoc_html,get_swagger_ui_html
        from fastapi.responses import JSONResponse as SchemaResponse
        @a.get('/openapi.json',include_in_schema=False)
        def openapi_schema(actor=Depends(user)):
            return SchemaResponse(a.openapi())
        @a.get('/docs',include_in_schema=False,response_class=HTMLResponse)
        def api_docs(actor=Depends(user)):
            return get_swagger_ui_html(openapi_url='/openapi.json',title=f'{title} — документация')
        @a.get('/redoc',include_in_schema=False,response_class=HTMLResponse)
        def api_redoc(actor=Depends(user)):
            return get_redoc_html(openapi_url='/openapi.json',title=f'{title} — документация')
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
        try:
            with engine().connect() as c:
                c.execute(text('SELECT 1'))
                # Обычный запуск схему не меняет: pending/чужая ревизия — отказ, не «ок».
                schema=describe_schema(c)
                if schema['state']!='ok': raise HTTPException(503,schema_error_message(schema))
                with_connection=c.execute(text("SELECT count(*) FROM impact_projects WHERE coalesce(web_url,'')<>'' OR coalesce(odata_url,'')<>''")).scalar()
                instance_db, instance_marker=c.execute(text(
                    "SELECT current_database(), shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname = current_database()"
                )).one()
        except HTTPException:
            raise
        except SQLAlchemyError:
            # Недоступная БД — честный отказ без stacktrace, DSN и секретов.
            raise HTTPException(503,'База данных недоступна: проверьте, что PostgreSQL запущен. Схема не проверена, сервис не готов.')
        instance_token=(os.environ.get('TP_INSTANCE_TOKEN') or '').strip()
        env_configured=bool((os.environ.get('TP_1C_ODATA_URL') or '').strip() or (os.environ.get('TP_1C_WEB_URL') or '').strip())
        from testpilot import audit_limit
        return {'status':'ok','executor_1c_env':'configured' if env_configured else 'not_configured',
                'projects_with_connection':int(with_connection or 0),
                'audit_max_issues':audit_limit.MAX_ISSUES_KEPT,
                'instance':{'database':instance_db,
                            'marker_ok':bool(instance_token) and instance_marker==instance_token}}
    return a

def require(value,detail='Запись не найдена'):
    if value is None: raise HTTPException(404,detail)
    return value


async def upload_body(request:Request) -> bytes:
    return await request.body()
