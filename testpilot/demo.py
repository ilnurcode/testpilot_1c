"""Только явно запрошенный synthetic/demo проект. Все вычисления выполняют API."""
import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
import httpx

CONFIG='demo-sales'

def configurations():
    names=[('order','document','Документ.ЗаказКлиента'),('client','catalog','Справочник.Контрагенты'),('item','catalog','Справочник.Номенклатура'),('prices','information_register','РегистрСведений.Цены'),('sales','accumulation_register','РегистрНакопления.Продажи'),('sales_module','common_module','ОбщийМодуль.Продажи'),('form','form','Документ.ЗаказКлиента.Форма'),('post','procedure','Продажи.Проведение'),('sum','function','Продажи.РассчитатьСумму'),('event','event_subscription','Подписка.ПослеЗаписи'),('legacy','procedure','Продажи.СтарыйРасчёт'),('audit','common_module','ОбщийМодуль.Аудит')]
    objects=[{'object_id':i,'type':t,'full_name':n,'parent_id':'order' if i=='form' else None,'attributes':{'revision':1}} for i,t,n in names]
    relationships=[('order','sales_module','uses'),('form','order','belongs_to'),('post','sum','calls'),('sales_module','post','calls'),('post','sales','uses'),('sum','prices','uses'),('order','client','uses'),('order','item','uses'),('event','order','subscribes_to'),('sales_module','audit','calls'),('audit','sales_module','calls'),('post','legacy','calls')]
    edges=[{'source':a,'target':b,'type':t,'origin':'synthetic:ручная учебная карта','confidence':1.0} for a,b,t in relationships]
    base={'schema_version':'1.0','configuration_id':CONFIG,'version':'1.0','source':'synthetic:учебный JSON','synthetic':True,'objects':objects,'edges':edges}
    import copy
    target=copy.deepcopy(base);target['version']='2.0'
    target['objects']=[o for o in target['objects'] if o['object_id']!='legacy']
    for o in target['objects']:
        if o['object_id'] in ('sum','prices'):o['attributes']['revision']=2
    target['objects'].append({'object_id':'discount','type':'function','full_name':'Продажи.Скидка','parent_id':None,'attributes':{'revision':1}})
    target['edges']=[e for e in target['edges'] if e['target']!='legacy']
    target['edges'].append({'source':'sum','target':'discount','type':'calls','origin':'synthetic:учебная карта','confidence':.8})
    return base,target

def catalog():
    definitions=[('Оформление заказа',['order','form'],70,'critical'),('Расчёт суммы',['sum','prices','discount'],45,'high'),('Проведение заказа',['post','sales'],90,'high'),('Сервис продаж',['sales_module','audit'],55,'medium'),('Удалённый расчёт',['legacy'],20,'medium'),('Событие записи',['event'],25,'medium'),('Карточка контрагента',['client'],80,'low'),('Номенклатура',['item'],75,'low'),('Расширенная форма',['form'],160,'medium'),('Полный расчёт',['sum','prices'],150,'high'),('Отчёт продаж',['sales'],180,'high'),('Журнал аудита',['audit'],140,'medium'),('Нагрузочный заказ',['order','post'],300,'low'),('Архивный сценарий',['order'],20,'low'),('Старая версия',['sum'],20,'high'),('Спорное покрытие',['discount'],20,'medium')]
    output=[]
    for i,(title,cover,duration,criticality) in enumerate(definitions,1):
        t={'test_id':f'demo-{i:02}','title':title,'description':'Синтетический учебный сценарий; реальный запуск 1С не выполнялся','active':i!=14,'criticality':criticality,'mandatory':i==1,'duration_seconds':duration,'configuration_id':CONFIG,'compatible_versions':['1.0'] if i==15 else [],'executor_parameters':{'scenario':f'demo-{i:02}'},'metadata':{'dataset':'synthetic'},'synthetic':True}
        output.append((t,cover,'unconfirmed' if i==16 else 'confirmed'))
    return output

def histories():
    rows=[]
    for day in range(12):
        stamp=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=day)
        results=[];defects=[]
        for i,(t,_,_) in enumerate(catalog(),1):
            if i in (14,15):continue
            failed=(i%3==0 and day%3!=0) or (i%3!=0 and day%7==0)
            duration=t['duration_seconds']
            results.append({'test_id':t['test_id'],'status':'failed' if failed else 'passed','duration_seconds':duration,'started_at':stamp.isoformat(),'finished_at':(stamp+timedelta(seconds=duration)).isoformat(),'error':'Синтетическая ошибка' if failed else '', 'artifacts':[]})
            if failed and day%4==0:
                defects.append({'defect_id':f'demo-defect-{day}-{i}','test_id':t['test_id'],'severity':t['criticality'],'state':'closed','description':'Учебный дефект'})
        rows.append({'run_id':f'demo-run-{day}','configuration_id':CONFIG,'version':'1.0','synthetic':True,'results':results,'defects':defects})
    return rows

async def run(impact_url,intelligence_url,auth=None,authorization=None,budget=330):
    headers={'Authorization':authorization} if authorization else {}
    async with httpx.AsyncClient(auth=auth,headers=headers,timeout=120) as client:
        async def call(method,url,**kwargs):
            r=await client.request(method,url,**kwargs)
            if r.is_error:raise RuntimeError(f'{method} {url}: {r.status_code} {r.text[:1000]}')
            return r.json()
        for config in configurations():await call('POST',impact_url+'/api/v1/imports',json=config)
        for t,cover,state in catalog():
            r=await client.get(intelligence_url+'/api/v1/tests/'+t['test_id'])
            if r.status_code==404:await call('POST',intelligence_url+'/api/v1/tests',json=t)
            elif r.is_error:raise RuntimeError('Невозможно прочитать каталог')
            # Не перезаписываем ранее принятые экспертные решения повторным демо.
            mappings=await call('GET',intelligence_url+'/api/v1/coverage')
            for oid in cover:
                if not any(m['test_id']==t['test_id'] and m['object_id']==oid for m in mappings):
                    await call('PUT',intelligence_url+'/api/v1/tests/'+t['test_id']+'/coverage',json={'object_id':oid,'state':state,'source':'synthetic:эталон','reason':'Учебная карта покрытия'})
        for h in histories():await call('POST',intelligence_url+'/api/v1/history',json=h)
        p=await call('POST',impact_url+'/api/v1/analyses',json={'configuration_id':CONFIG,'base_version':'1.0','target_version':'2.0','max_depth':10})
        delivery=await call('POST',impact_url+'/api/v1/analyses/'+p['analysis_id']+'/deliver')
        plan=await call('POST',intelligence_url+'/api/v1/plans',json={'package_id':p['package_id'],'budget_seconds':budget,'min_coverage':.9,'use_ml':False})
        assert p['changed_objects'] and 'order' in p['affected_objects']
        assert 'demo-01' in plan['selected_ids']
        assert plan['duration_seconds']<=budget and plan['coverage_available']>=.9 and plan['status']=='feasible',plan
        return {'analysis_id':p['analysis_id'],'package_id':p['package_id'],'plan_id':plan['plan_id'],'changed_objects':len(p['changed_objects']),'affected_objects':len(p['affected_objects']),'selected_ids':plan['selected_ids'],'duration_seconds':plan['duration_seconds'],'full_duration_seconds':plan['full_duration_seconds'],'coverage_available':plan['coverage_available'],'coverage_all':plan['coverage_all'],'status':plan['status'],'synthetic':True,'executor_status':'Исполнитель 1С не подключён'}

def main():
    parser=argparse.ArgumentParser(description='Загрузка синтетического demo-sales через HTTP API')
    parser.add_argument('--budget',type=float,default=330)
    args=parser.parse_args()
    import json
    print(json.dumps(asyncio.run(run(os.environ.get('IMPACT_URL','http://127.0.0.1:8001'),os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002'),auth=('expert',os.environ['TP_EXPERT_PASSWORD']),budget=args.budget)),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
