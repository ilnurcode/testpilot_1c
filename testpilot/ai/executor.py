"""Ограниченный исполнитель учебного стенда. Произвольный код LLM не выполняется."""
import os
import time
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
import httpx
from testpilot.contracts import now


def browser_path(playwright_instance=None):
    configured=os.environ.get('TP_CHROMIUM_PATH')
    if configured:
        return configured if Path(configured).is_file() else None
    if os.name!='nt' and Path('/usr/bin/chromium').is_file():
        return '/usr/bin/chromium'
    if playwright_instance is not None:
        path=playwright_instance.chromium.executable_path
        return path if Path(path).is_file() else None
    # Playwright supplies its managed Chromium on Windows and minimal containers.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        path=p.chromium.executable_path
    return path if Path(path).is_file() else None


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
                        browser=p.chromium.launch(executable_path=path,headless=True,args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
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


def execute_1c(suite, profile):
    """Исполнение сценариев на живой базе 1С:Предприятия через OData и WebClient."""
    from testpilot.integration.odata import ODataClient, NotFoundError
    from testpilot.integration.webclient import WebClientRunner

    results = []
    evidence = []
    odata_client = ODataClient(profile) if profile.odata_url else None
    web_runner = WebClientRunner(profile) if profile.web_url else None

    for index in suite['approval']['selected_proposals']:
        proposal = suite['suggestion']['proposals'][index]
        for case_index, case in enumerate(proposal.get('cases') or [{}]):
            start = time.perf_counter()
            started = now()
            result = {
                'proposal_index': index,
                'case_index': case_index,
                'title': proposal['title'],
                'engine': proposal['engine'],
                'input': case,
                'expected': case.get('expected'),
                'started_at': started,
                'status': 'error',
                'synthetic': False
            }

            try:
                if proposal['engine'] == 'manual':
                    result.update(status='skipped', message='Требует ручной проверки в 1С; автозапуск пропущен')
                elif proposal['engine'] == 'odata':
                    if not odata_client:
                        raise RuntimeError("Адрес OData 1С не настроен")
                    if case.get('check')=='metadata' or not case.get('entity'):
                        entity_sets=odata_client.get_metadata()
                        result.update(
                            actual=len(entity_sets),
                            status='passed',
                            message=(
                                "Сервис OData доступен, но не публикует ни одной сущности — чтение данных не проверено"
                                if not entity_sets else
                                f"Проверка OData $metadata пройдена: EntitySet — {len(entity_sets)}"
                            ),
                        )
                    else:
                        entity=case['entity']
                        try:
                            items=odata_client.get_entities(entity,top=5)
                        except NotFoundError:
                            result.update(status='failed',message=f"Сущность OData {entity} не найдена (HTTP 404)")
                        else:
                            result['actual']=len(items)
                            result['status']='passed'
                            result['message']=f"Успешный OData-запрос к {entity}: получено записей: {len(items)}"
                    # Адрес, который реально ответил: при откате это исходный loopback, а не mapped-хост.
                    result['odata_url']=odata_client.last_base_url
                else:
                    if not web_runner:
                        raise RuntimeError("Адрес веб-клиента 1С не настроен")
                    steps = case.get('steps') or [{'action': 'wait', 'value': 2000}]
                    report = web_runner.run_scenario(title=proposal['title'], steps=steps)
                    result['status'] = report.status
                    # Фактический адрес публикации: при откате с mapped-хоста это исходный loopback.
                    result['web_url'] = report.url
                    section_result = next(
                        (step for step in reversed(report.steps) if step.action == 'assert_section' and step.status == 'passed'),
                        None,
                    )
                    result['message'] = report.error or (
                        section_result.message if section_result else
                        f"Сценарий в веб-клиенте 1С выполнен ({len(report.steps)} шагов)"
                    )
                    if getattr(web_runner, 'last_login_diagnostics', None):
                        result['login'] = dict(web_runner.last_login_diagnostics)
                    attached = False
                    tiny = False
                    # Снимки разделены по стадии: pre_submit не считается ни пост-кликовым, ни снимком шага.
                    shots = list(report.screenshots)
                    stages = list(getattr(report, 'screenshot_stages', []) or [])
                    if len(stages) < len(shots):
                        stages += ['step_failure'] * (len(shots) - len(stages))
                    pre = getattr(report, 'pre_submit_screenshot', b'') or b''

                    def usable(shot: bytes) -> bool:
                        return bool(shot) and 100 <= len(shot) <= 2 * 1024 * 1024

                    step_pairs = [(shot, stage) for shot, stage in zip(shots, stages)
                                  if stage != 'pre_submit' and usable(shot)]
                    pre_pairs = [(shot, stage) for shot, stage in zip(shots, stages)
                                 if stage == 'pre_submit' and usable(shot)]
                    if usable(pre):
                        pre_pairs.append((pre, 'pre_submit'))

                    chosen = step_pairs[-1][0] if step_pairs else (pre_pairs[-1][0] if pre_pairs else b'')
                    post_click_sizes = [len(shot) for shot, stage in zip(shots, stages) if stage == 'post_click']
                    result['evidence_provenance'] = (
                        'step' if step_pairs else ('pre_submit' if pre_pairs else 'none')
                    )
                    result['post_click_screenshot_bytes'] = post_click_sizes[-1] if post_click_sizes else 0
                    result['pre_submit_screenshot_bytes'] = len(pre_pairs[-1][0]) if pre_pairs else 0
                    if chosen:
                        evidence.append((len(results), chosen))
                        attached = True
                    all_sizes = [len(shot) for shot in shots] + ([len(pre)] if pre else [])
                    tiny = any(size < 100 for size in all_sizes)
                    if not attached:
                        result['screenshot_unavailable'] = True
                        result['screenshot_unavailable_reason'] = (
                            'снимок пуст: страница не отрисована или браузер закрылся'
                            if tiny else
                            'скриншот не получен: браузер закрылся до снимка или страница не отрисовалась'
                        )
            except Exception as exc:
                result['status'] = 'error'
                result['message'] = f"Ошибка выполнения в 1С: {exc}"

            result.update(finished_at=now(), duration_seconds=round(time.perf_counter() - start, 3))
            results.append(result)

    return results, evidence
