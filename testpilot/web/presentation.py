"""Отдельный synthetic-стенд для воспроизводимой презентации; не публикация 1С."""
import copy
import io
import json
import os
from decimal import Decimal,ROUND_HALF_UP
from zipfile import ZipFile
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse,Response
from pydantic import Field
from testpilot.common import analyst,expert,user,require
from testpilot.contracts import Model
from testpilot.db import session,DemoOrderRow
from testpilot.demo import configurations

router=APIRouter()
class Order(Model):
    quantity:int=Field(ge=1,le=1000)
    price:float=Field(ge=0,le=1000000)
    discount:float=Field(ge=0,le=100)

def sample(which):
    a,b=copy.deepcopy(configurations())
    for p in (a,b):p['configuration_id']='presentation-sales';p['source']='synthetic:presentation'
    for p in (a,b):
        for o in p['objects']:
            if o['object_id']=='sum':o['attributes']['description']='Расчёт суммы заказа с процентной скидкой, включая оптовые заказы.'
    return a if which==1 else b

@router.get('/api/presentation/sample/{which}')
def download_sample(which:int,actor=Depends(user)):
    if which not in (1,2):raise HTTPException(404,'Образец не найден')
    out=io.BytesIO()
    with ZipFile(out,'w') as z:z.writestr('configuration.json',json.dumps(sample(which),ensure_ascii=False,indent=2))
    return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename=presentation-v{which}.zip'})

@router.post('/api/presentation')
async def prepare(request:Request,actor=Depends(expert)):
    base=os.environ.get('IMPACT_URL','http://127.0.0.1:8001')+'/api/v1'
    async with httpx.AsyncClient(timeout=60,headers={'Authorization':request.headers['authorization']}) as c:
        projects=await c.get(base+'/projects');projects.raise_for_status()
        project=next((p for p in projects.json() if p['configuration_id']=='presentation-sales'),None)
        if not project:
            r=await c.post(base+'/projects',json={'name':'Продажи — презентационный стенд','description':'Учебная разработка: версии, зависимости, сценарии и реальные проверки на синтетическом стенде. Не реальная 1С.'});r.raise_for_status();project=r.json()
        uploads=[]
        for version in (1,2):
            raw=json.dumps(sample(version),ensure_ascii=False).encode()
            r=await c.post(base+f'/projects/{project["id"]}/uploads',params={'filename':f'Учебная-разработка-v{version}.json','label':f'{version}.0','kind':'extension','component':'Расширение продаж'},content=raw);r.raise_for_status();uploads.append(r.json())
        r=await c.post(base+f'/projects/{project["id"]}/uploads/{uploads[1]["id"]}/analyze',json={'baseline_id':uploads[0]['id']});r.raise_for_status()
        return {'project_id':project['id'],'analysis':r.json(),'synthetic':True}

@router.post('/api/sandbox/orders')
def create_order(q:Order,actor=Depends(analyst),s=Depends(session,scope='function')):
    # Преднамеренная ошибка ТОЛЬКО учебного приложения: ветка оптового заказа теряет скидку.
    discount=Decimal(str(q.discount)) if q.quantity<10 else Decimal(0)
    total=(Decimal(q.quantity)*Decimal(str(q.price))*(1-discount/100)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    row=DemoOrderRow(payload={**q.model_dump(),'total':float(total),'synthetic':True});s.add(row);s.flush()
    return {'id':row.id,**row.payload,'@odata.context':'synthetic/demo_orders'}

@router.get('/api/sandbox/orders/{oid}')
def get_order(oid:str,actor=Depends(user),s=Depends(session,scope='function')):
    row=require(s.get(DemoOrderRow,oid));return {'id':row.id,**row.payload}

@router.delete('/api/sandbox/orders/{oid}')
def delete_order(oid:str,actor=Depends(analyst),s=Depends(session,scope='function')):
    row=require(s.get(DemoOrderRow,oid));s.delete(row);return {'deleted':True,'synthetic':True}

@router.get('/sandbox/sales',response_class=HTMLResponse)
def sandbox(actor=Depends(user)):
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Учебный стенд продаж</title>
<style>body{font:16px system-ui;background:#f3f6fc;color:#253955;margin:0}header{background:#244f99;color:white;padding:22px 40px}main{max-width:700px;margin:45px auto;background:white;border:1px solid #dfe7f3;border-radius:14px;padding:32px}label{display:block;margin:17px 0;color:#617792}input{display:block;padding:12px;border:1px solid #ccd8ec;border-radius:6px;width:90%;margin-top:7px}button{background:#3563e9;color:white;padding:13px 20px;border:0;border-radius:7px;cursor:pointer}#result{margin-top:25px;padding:24px;background:#eef4ff;border-radius:10px}#total{font-size:32px;font-weight:650}.badge{background:#fff3d5;color:#8d6d2e;padding:8px;font-size:13px;border-radius:5px}small{color:#8a9bb2}</style>
<header>Учебный стенд · Продажи</header><main><span class="badge">Синтетические данные · не 1С</span><h1>Расчёт заказа клиента</h1><p>Проверяемое приложение с намеренной регрессией в оптовом расчёте.</p><form id="order"><label>Количество<input id="quantity" type="number" value="2" min="1" max="1000" required></label><label>Цена<input id="price" type="number" value="100" min="0" step="0.01" required></label><label>Скидка, %<input id="discount" type="number" value="10" min="0" max="100" required></label><button>Рассчитать заказ</button></form><div id="result" hidden><small>Итоговая сумма</small><div><span id="total"></span> ₽</div><p>Данные сохранены в отдельной учебной таблице.</p></div></main><script>document.querySelector('#order').onsubmit=async e=>{e.preventDefault();const body={};for(const id of ['quantity','price','discount'])body[id]=Number(document.getElementById(id).value);const r=await fetch('/api/sandbox/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)return;const v=await r.json();document.querySelector('#total').textContent=v.total;const result=document.querySelector('#result');result.hidden=false;result.dataset.ready='true';result.dataset.order=v.id;};</script></html>'''
