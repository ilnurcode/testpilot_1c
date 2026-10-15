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

@app.api_route('/gateway/{service}/{path:path}',methods=['GET','POST','PUT','DELETE'])
async def gateway(service:str,path:str,request:Request,actor=Depends(user)):
    if service not in ('impact','intelligence') or not path.startswith('api/v1/') or '..' in path or '\\' in path:
        raise HTTPException(404,'Неизвестный API')
    base=os.environ.get('IMPACT_URL','http://127.0.0.1:8001') if service=='impact' else os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002')
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r=await c.request(request.method,base+'/'+path,params=request.query_params,content=await request.body(),headers={'Authorization':request.headers['authorization'],'Content-Type':request.headers.get('content-type','application/json')})
    except httpx.HTTPError:raise HTTPException(503,'Сервис временно недоступен')
    headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','content-disposition','x-content-type-options','x-checksum-sha256')}
    return Response(r.content,status_code=r.status_code,headers=headers)

@app.post('/api/demo')
async def demo(request:Request,actor=Depends(expert)):
    try:return await run(os.environ.get('IMPACT_URL','http://127.0.0.1:8001'),os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002'),authorization=request.headers['authorization'])
    except (RuntimeError,AssertionError) as e:raise HTTPException(502,str(e))

from testpilot.web.presentation import router as presentation_router
app.include_router(presentation_router)
