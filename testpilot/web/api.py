import os
from pathlib import Path
import httpx
from fastapi import Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from testpilot.common import app, user, expert
from testpilot.demo import run

app=app('TestPilot 1C — интерфейс')
STATIC=Path(__file__).parent/'static'
app.mount('/static',StaticFiles(directory=STATIC),name='static')

@app.middleware('http')
async def same_origin(request,call_next):
    if request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return Response('Межсайтовый запрос запрещён',status_code=403)
    return await call_next(request)

@app.get('/',response_class=HTMLResponse)
def home(actor=Depends(user)):
    return (STATIC/'index.html').read_text()

@app.get('/api/whoami')
def whoami(actor=Depends(user)):return {'role':actor}

async def gateway_response(service:str,path:str,request:Request,actor:str)->Response:
    if service not in ('impact','intelligence') or not path.startswith('api/v1/') or '..' in path or '\\' in path:
        raise HTTPException(404,'Неизвестный API')
    base=os.environ.get('IMPACT_URL','http://127.0.0.1:8001') if service=='impact' else os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002')
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            r=await c.request(request.method,base+'/'+path,params=request.query_params,content=await request.body(),headers={'Authorization':request.headers['authorization'],'Content-Type':request.headers.get('content-type','application/json')})
    except httpx.HTTPError:raise HTTPException(503,'Сервис временно недоступен')
    headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','content-disposition','x-content-type-options','x-checksum-sha256')}
    return Response(r.content,status_code=r.status_code,headers=headers)

# Один маршрут на метод: FastAPI генерирует operationId на маршрут, поэтому общий api_route
# с четырьмя методами давал одинаковый operationId и ломал генераторы клиентов.
@app.get('/gateway/{service}/{path:path}',operation_id='gateway_get')
async def gateway_get(service:str,path:str,request:Request,actor=Depends(user)):
    return await gateway_response(service,path,request,actor)

@app.post('/gateway/{service}/{path:path}',operation_id='gateway_post')
async def gateway_post(service:str,path:str,request:Request,actor=Depends(user)):
    return await gateway_response(service,path,request,actor)

@app.put('/gateway/{service}/{path:path}',operation_id='gateway_put')
async def gateway_put(service:str,path:str,request:Request,actor=Depends(user)):
    return await gateway_response(service,path,request,actor)

@app.delete('/gateway/{service}/{path:path}',operation_id='gateway_delete')
async def gateway_delete(service:str,path:str,request:Request,actor=Depends(user)):
    return await gateway_response(service,path,request,actor)

@app.post('/api/demo')
async def demo(request:Request,actor=Depends(expert)):
    try:return await run(os.environ.get('IMPACT_URL','http://127.0.0.1:8001'),os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002'),authorization=request.headers['authorization'])
    except (RuntimeError,AssertionError) as e:raise HTTPException(502,str(e))

from testpilot.web.presentation import router as presentation_router
app.include_router(presentation_router)

from testpilot.web.audit_api import router as audit_router
app.include_router(audit_router)

from testpilot.web.audit_report_api import router as audit_report_router
app.include_router(audit_report_router)
