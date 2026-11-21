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


def _typed_actual(value, expected_type=None):
    if value is None:
        return {'type': 'null', 'value': None}
    if isinstance(value, bool):
        return {'type': 'boolean', 'value': value}
    if isinstance(value, int) and not isinstance(value, bool):
        if expected_type == 'number':
            return {'type': 'number', 'value': Decimal(value)}
        return {'type': 'integer', 'value': value}
    if isinstance(value, (Decimal, float)):
        return {'type': 'number', 'value': Decimal(str(value))}
    if expected_type == 'number' and isinstance(value, str):
        try:
            return {'type': 'number', 'value': Decimal(value)}
        except Exception:
            return {'type': 'string', 'value': value}
    if expected_type == 'date' and isinstance(value, str):
        from datetime import date, datetime
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return {'type': 'date', 'value': parsed.isoformat()}
        except ValueError:
            try:
                return {'type': 'date', 'value': date.fromisoformat(value).isoformat()}
            except ValueError:
                return {'type': 'string', 'value': value}
    return {'type': 'string', 'value': str(value)}


def _field_path(payload, path):
    current = payload
    for part in (path or '').split('.'):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            raise KeyError(part)
        current = current[part]
    return current


def _missing_actual():
    return {'type': 'missing', 'value': None}


def _compare(expected, actual):
    if expected.type == 'null':
        return actual.get('type') == 'null'
    if expected.type != actual.get('type'):
        return False
    if expected.type == 'number':
        if actual.get('type') not in ('integer', 'number'):
            return False
        try:
            return Decimal(str(actual['value'])) == expected.value
        except Exception:
            return False
    if expected.type == 'date':
        return actual.get('type') == 'date' and actual.get('value') == expected.value
    return actual['value'] == expected.value


def evaluate_assertions(assertions, odata_client=None, web_runner=None, web_actuals=None, should_cancel=None):
    """Типизированные проверки: бизнес-успех только по business expected/actual."""
    from testpilot.ai.models import Assertion

    results = []
    for raw in assertions or []:
        if should_cancel and should_cancel():
            break
        assertion = Assertion.model_validate(raw)
        expected = None if assertion.expected is None else assertion.expected.model_dump(mode='json')
        detail = {'assertion_id': assertion.id, 'scope': assertion.scope,
                  'status': 'error', 'expected': expected, 'actual': None, 'message': ''}
        try:
            if assertion.check == 'manual':
                detail.update(status='skipped', message=assertion.requirement or 'Требует ручной проверки')
            elif assertion.check == 'metadata_entity':
                if not odata_client:
                    raise RuntimeError('OData не настроен')
                names = {item.get('name') for item in odata_client.get_metadata()}
                actual = assertion.selector.entity in names
                detail.update(actual={'type': 'boolean', 'value': actual})
                detail['status'] = 'passed' if actual else 'failed'
                detail['message'] = 'EntitySet присутствует в metadata' if actual else 'EntitySet не найден в metadata'
            elif assertion.check == 'odata_record_count':
                if not odata_client:
                    raise RuntimeError('OData не настроен')
                actual = odata_client.get_count(assertion.selector.entity)
                detail.update(actual={'type': 'integer', 'value': actual})
                detail['status'] = 'passed' if actual == assertion.expected.value else 'failed'
                detail['message'] = f'Записей: {actual}; ожидалось {assertion.expected.value}'
            elif assertion.check in ('odata_field_equals', 'odata_posted_state'):
                if not odata_client:
                    raise RuntimeError('OData не настроен')
                entity = odata_client.get_entity(assertion.selector.entity, assertion.selector.key)
                field = assertion.selector.field or 'Posted'
                try:
                    actual = _typed_actual(_field_path(entity, field), assertion.expected.type)
                except KeyError:
                    actual = _missing_actual()
                detail.update(actual=actual)
                ok = _compare(assertion.expected, actual)
                detail['status'] = 'passed' if ok else 'failed'
                detail['message'] = f'{field}: фактически {actual.get("value")!r}; ожидалось {assertion.expected.value!r}'
            elif assertion.check == 'web_section_visible':
                section = assertion.selector.section or (assertion.selector.path[-1] if assertion.selector.path else '')
                if web_actuals is not None:
                    observation = web_actuals.get(assertion.id) or {}
                    if observation.get('status') == 'error':
                        raise RuntimeError(observation.get('error') or 'Не удалось прочитать состояние веб-клиента')
                    actual_value = (web_actuals.get(assertion.id) or {}).get('value')
                    actual = {'type': 'boolean', 'value': actual_value} if isinstance(actual_value, bool) else _missing_actual()
                    detail.update(actual=actual)
                    detail['status'] = 'passed' if _compare(assertion.expected, actual) else 'failed'
                    detail['message'] = f'Секция {section!r}: фактически {actual_value!r}'
                else:
                    if not web_runner:
                        raise RuntimeError('WebClient не настроен')
                    report = web_runner.run_scenario('typed section probe', [{'action': 'assert_section', 'target': section}])
                    if report.status == 'error':
                        raise RuntimeError(report.error or 'Ошибка чтения веб-клиента')
                    actual = {'type': 'boolean', 'value': report.status == 'passed'}
                    detail.update(actual=actual)
                    detail['status'] = 'passed' if _compare(assertion.expected, actual) else 'failed'
                    detail['message'] = f'Секция {section!r}: {report.error or report.status}'
            else:
                raise RuntimeError('неизвестный check')
        except Exception as exc:
            detail['status'] = 'error'
            detail['message'] = f'{type(exc).__name__}: {exc}'
        results.append(detail)
    business = [r for r in results if r['scope'] == 'business']
    required_business = [r for r in business if _required_for(assertions, r['assertion_id'])]
    required_probe = [r for r in results if r['scope'] in ('metadata', 'connectivity') and _required_for(assertions, r['assertion_id'])]
    if required_business:
        if any(r['status'] == 'error' for r in required_business + required_probe):
            status, business_success = 'error', None
        elif any(r['status'] == 'failed' for r in required_business):
            status, business_success = 'failed', False
        elif any(r['status'] == 'failed' for r in required_probe):
            status, business_success = 'error', None
        else:
            status, business_success = 'passed', True
    elif required_probe:
        if any(r['status'] == 'error' for r in required_probe):
            status, business_success = 'error', None
        elif any(r['status'] == 'failed' for r in required_probe):
            status, business_success = 'failed', None
        else:
            status, business_success = 'passed', None
    elif any(r['scope'] == 'manual' and _required_for(assertions, r['assertion_id']) for r in results):
        status, business_success = 'skipped', None
    else:
        status, business_success = 'skipped', None
    return status, business_success, results


def _required_for(assertions, assertion_id):
    for raw in assertions or []:
        if raw.get('id') == assertion_id:
            return bool(raw.get('required', True))
    return True


def _usable_shot(shot):
    """Снимок пригоден как evidence: непустой и в разумных пределах размера."""
    return bool(shot) and 100 <= len(shot) <= 2 * 1024 * 1024


def _record_evidence(result, report, results, evidence):
    """Прикрепляет к результату ровно один снимок и фиксирует, откуда он взялся.

    Стадии снимков разделены: pre_submit не считается ни пост-кликовым снимком, ни снимком шага.
    Пустой или крошечный снимок как evidence не сохраняется; вместо него результат получает
    честную причину отсутствия, иначе отчёт показывал бы пустые колонки при живом прогоне.
    """
    shots = list(report.screenshots or [])
    pre = getattr(report, 'pre_submit_screenshot', b'') or b''
    if report.status == 'cancelled' and not shots and not pre:
        # Отмена до браузера: снимка не ждали, и «причина отсутствия» здесь была бы выдумкой.
        return
    stages = list(getattr(report, 'screenshot_stages', []) or [])
    if len(stages) < len(shots):
        stages += ['step_failure'] * (len(shots) - len(stages))

    step_pairs = [(shot, stage) for shot, stage in zip(shots, stages)
                  if stage != 'pre_submit' and _usable_shot(shot)]
    pre_pairs = [(shot, stage) for shot, stage in zip(shots, stages)
                 if stage == 'pre_submit' and _usable_shot(shot)]
    if _usable_shot(pre):
        pre_pairs.append((pre, 'pre_submit'))

    chosen = step_pairs[-1][0] if step_pairs else (pre_pairs[-1][0] if pre_pairs else b'')
    post_click_sizes = [len(shot) for shot, stage in zip(shots, stages) if stage == 'post_click']
    result['evidence_provenance'] = 'step' if step_pairs else ('pre_submit' if pre_pairs else 'none')
    result['post_click_screenshot_bytes'] = post_click_sizes[-1] if post_click_sizes else 0
    result['pre_submit_screenshot_bytes'] = len(pre_pairs[-1][0]) if pre_pairs else 0
    if chosen:
        evidence.append((len(results), chosen))
        return
    tiny = any(len(shot) < 100 for shot in shots) or (bool(pre) and len(pre) < 100)
    result['screenshot_unavailable'] = True
    result['screenshot_unavailable_reason'] = (
        'снимок пуст: страница не отрисована или браузер закрылся'
        if tiny else
        'скриншот не получен: браузер закрылся до снимка или страница не отрисовалась'
    )


def execute_1c(suite, profile, should_cancel=None, on_progress=None):
    """Исполнение сценариев на живой базе 1С:Предприятия через OData и WebClient."""
    from testpilot.integration.odata import ODataClient, NotFoundError
    from testpilot.integration.webclient import WebClientRunner

    results = []
    evidence = []
    odata_client = ODataClient(profile, should_cancel=should_cancel) if profile.odata_url else None
    web_runner = WebClientRunner(profile) if profile.web_url else None

    for index in suite['approval']['selected_proposals']:
        if should_cancel and should_cancel():
            break
        proposal = suite['suggestion']['proposals'][index]
        for case_index, case in enumerate(proposal.get('cases') or [{}]):
            if should_cancel and should_cancel():
                break
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

            evidence_start = len(evidence)
            try:
                engine = proposal.get('engine')
                assertions = case.get('assertions') or []
                if engine not in ('odata', 'playwright', 'manual'):
                    result.update(status='error', message=f'Неизвестный engine {engine!r}; запуск запрещён')
                elif engine == 'manual' and any(item.get('scope') != 'manual' for item in assertions):
                    result.update(status='error', message='manual engine не может выполнять auto assertions')
                elif assertions:
                    reports = []
                    web_assertions = [item for item in assertions if item.get('check') == 'web_section_visible']
                    steps = case.get('steps') or []
                    def step_progress(step_result, shots):
                        if on_progress is None:
                            return True
                        return on_progress(
                            {**step_result, 'proposal_index': index, 'case_index': case_index},
                            shots,
                        )
                    needs_browser = bool(web_assertions or (proposal['engine'] == 'playwright' and steps))
                    if needs_browser:
                        if not web_runner:
                            raise RuntimeError('Адрес веб-клиента 1С не настроен')
                        report = web_runner.run_scenario(title=proposal['title'], steps=steps,
                                                         assertions=web_assertions, should_cancel=should_cancel,
                                                         on_step=step_progress)
                        result['web_url'] = report.url
                        if getattr(web_runner, 'last_login_diagnostics', None):
                            result['login'] = dict(web_runner.last_login_diagnostics)
                        _record_evidence(result, report, results, evidence)
                        if report.status == 'cancelled':
                            result.update(status='cancelled', message=report.error or 'Отменено')
                        elif report.status in ('failed', 'error'):
                            result.update(status='error', message=report.error or 'Действия веб-клиента не выполнены')
                        reports.append(report)
                    if reports and reports[0].status in ('cancelled', 'error', 'failed'):
                        status, business_success, details = 'skipped', None, []
                    else:
                        status, business_success, details = evaluate_assertions(
                            assertions, odata_client, web_runner if not reports else None,
                            web_actuals=(reports[0].assertions if reports else None),
                            should_cancel=should_cancel,
                        )
                    if should_cancel and should_cancel():
                        result.update(status='cancelled', business_success=None,
                                      business_checked=False, assertions=details,
                                      message='Отменено до assertions; частичные действия сохранены')
                    elif reports and reports[0].status in ('cancelled', 'error', 'failed'):
                        # Сбой шага/входа — это ошибка исполнения, а не расхождение бизнес-проверки:
                        # статус failed выставляет только evaluate_assertions по business-scope.
                        result.update(status='cancelled' if reports[0].status == 'cancelled' else 'error',
                                      business_success=None, business_checked=False, assertions=details)
                    else:
                        result.update(status=status, business_success=business_success,
                                      business_checked=business_success is not None,
                                      assertions=details)
                    business = next((item for item in details if item['scope'] == 'business'), None)
                    if business:
                        result['expected'] = business.get('expected')
                        result['actual'] = business.get('actual')
                    result['message'] = result.get('message') or (
                        reports[0].error if reports and reports[0].error else
                        '; '.join(item['message'] for item in details if item.get('message'))[:500]
                    )
                elif proposal['engine'] == 'manual':
                    result.update(status='skipped', message='Требует ручной проверки в 1С; автозапуск пропущен')
                else:
                    result.update(status='error', message='Нет typed assertions: требуется повторное согласование')
            except Exception as exc:
                result['status'] = 'error'
                result['message'] = f"Ошибка выполнения в 1С: {exc}"

            result.update(finished_at=now(), duration_seconds=round(time.perf_counter() - start, 3))
            results.append(result)
            if on_progress:
                keep = on_progress(result, [content for _, content in evidence[evidence_start:]])
                if keep is False:
                    return results, evidence

    return results, evidence
