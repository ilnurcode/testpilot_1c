"""Ограниченный исполнитель учебного стенда. Произвольный код LLM не выполняется."""
import os
import time
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
import httpx
from testpilot.contracts import now


def browser_path():
    configured=os.environ.get('TP_CHROMIUM_PATH','/usr/bin/chromium')
    return configured if Path(configured).is_file() else None


def execute(suite,authorization):
    base=os.environ.get('WEB_URL','http://127.0.0.1:8000').rstrip('/')
    results=[];evidence=[]
    for index in suite['approval']['selected_proposals']:
        proposal=suite['suggestion']['proposals'][index]
        for case_index,case in enumerate(proposal['cases'] or [{}]):
            start=time.perf_counter();started=now();order_id=None
            result={'proposal_index':index,'case_index':case_index,'title':proposal['title'],'engine':proposal['engine'],'input':case,'expected':case.get('expected_total'),'started_at':started,'status':'error','synthetic':True}
            try:
                if proposal['engine']=='manual':result.update(status='skipped',message='Требует ручной проверки; автоматический запуск не выполнялся')
                elif proposal['engine']=='odata':
                    with httpx.Client(timeout=15,headers={'Authorization':authorization},follow_redirects=False) as c:
                        r=c.post(base+'/api/sandbox/orders',json={k:case[k] for k in ('quantity','price','discount')});r.raise_for_status()
                        order_id=r.json()['id'];r=c.get(base+'/api/sandbox/orders/'+order_id);r.raise_for_status();result['actual']=r.json()['total']
                else:
                    path=browser_path()
                    if not path:raise RuntimeError('Chromium не установлен в окружении исполнителя')
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser=p.chromium.launch(executable_path=path,headless=True,args=['--no-sandbox'])
                        try:
                            context=browser.new_context(extra_http_headers={'Authorization':authorization},viewport={'width':1100,'height':760},locale='ru-RU')
                            page=context.new_page();page.set_default_timeout(12000);page.goto(base+'/sandbox/sales')
                            for name in ('quantity','price','discount'):page.locator('#'+name).fill(str(case[name]))
                            page.get_by_role('button',name='Рассчитать заказ',exact=True).click()
                            page.locator('#result[data-ready="true"]').wait_for()
                            result['actual']=float(page.locator('#total').inner_text());order_id=page.locator('#result').get_attribute('data-order')
                            shot=page.screenshot(full_page=True)
                            if len(shot)<=2*1024*1024:evidence.append((len(results),shot))
                        finally:browser.close()
                if 'actual' in result:
                    expected=Decimal(str(case['expected_total'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    actual=Decimal(str(result['actual'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    result['status']='passed' if actual==expected else 'failed'
                    result['message']='Ожидаемая и фактическая суммы совпали' if actual==expected else 'Сумма заказа не совпала с ожидаемой'
            except (httpx.HTTPError,RuntimeError,ImportError):result['message']='Не удалось выполнить проверку. Проверьте доступность учебного стенда и установку Playwright/Chromium.'
            except Exception:result['message']='Ошибка браузерного шага; проверка не считается пройденной.'
            finally:
                if order_id:
                    try:
                        with httpx.Client(timeout=5,headers={'Authorization':authorization}) as c:
                            result['cleanup_succeeded']=c.delete(base+'/api/sandbox/orders/'+order_id).status_code==200
                    except httpx.HTTPError:result['cleanup_succeeded']=False
            result.update(finished_at=now(),duration_seconds=round(time.perf_counter()-start,3));results.append(result)
    return results,evidence
