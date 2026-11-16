"""Дополнительная проверка: pip install playwright, установленный Chromium."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(http_credentials={'username':'expert','password':os.environ['TP_EXPERT_PASSWORD']},viewport={'width':1440,'height':1000})
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(os.environ.get('WEB_URL','http://127.0.0.1:8000'))
    page.get_by_role('heading',name='Что изменилось и что проверить').wait_for()
    page.get_by_text('Учебная демонстрация полного сценария',exact=True).click()
    page.get_by_role('button',name='Выполнить учебный сценарий',exact=True).click()
    page.get_by_text('Сценарий пройден.',exact=False).wait_for(timeout=60000)
    for label,heading in [('Проекты и файлы','Проекты и файлы'),('Сравнение','Что изменилось?')]:
        page.locator('nav').get_by_role('button',name=label,exact=True).click();page.get_by_role('heading',name=heading,exact=True).wait_for()
    page.get_by_role('button',name='Сравнить версии',exact=True).click();page.locator('#compareResult').get_by_text('Изменён',exact=True).first.wait_for()
    page.locator('nav').get_by_role('button',name='Граф и влияние',exact=True).click();page.get_by_role('heading',name='На что влияют изменения?',exact=True).wait_for()
    page.get_by_role('button',name='Рассчитать влияние',exact=True).click();page.locator('#impactResult').get_by_role('heading',name='Зона: 10 объектов').wait_for()
    assert page.locator('#graph circle').count()==12
    page.locator('#graph circle').first.click();page.locator('#nodeDetails h3').wait_for()
    page.get_by_role('button',name='Передать последний анализ во второй сервис',exact=True).click();page.locator('#impactResult').get_by_text('передан по HTTP.',exact=False).wait_for()
    page.locator('nav').get_by_role('button',name='Каталог и покрытия',exact=True).click();page.get_by_role('heading',name='Каталог и подтверждённое покрытие').wait_for();page.locator('.edit-test').first.wait_for();assert page.locator('.edit-test[data-id^="demo-"]').count()==16
    page.locator('nav').get_by_role('button',name='История и дефекты',exact=True).click();page.get_by_role('heading',name='История запусков и дефекты').wait_for()
    page.locator('nav').get_by_role('button',name='Расчёт тест-плана',exact=True).click();page.get_by_role('heading',name='Почему выбраны эти тесты?').wait_for()
    page.get_by_role('button',name='Рассчитать план',exact=True).click();page.wait_for_function("!document.querySelector('#nav button').disabled && document.querySelector('#message').textContent===''");page.locator('#planResult').get_by_text('Ограничения выполнены',exact=False).wait_for()
    assert page.locator('.selected-test:checked').count()==6
    page.locator('#approvalReason').fill('Проверка интерфейса на синтетическом наборе')
    page.get_by_role('button',name='Подтвердить выбранный состав',exact=True).click();page.get_by_role('button',name='Сформировать задание будущему исполнителю',exact=True).wait_for()
    page.get_by_role('button',name='Сформировать задание будущему исполнителю',exact=True).click();page.locator('#jobResult').get_by_text('Это экспорт задания, а не запуск 1С.',exact=True).wait_for()
    assert page.locator('h1').inner_text()=='Почему выбраны эти тесты?'
    assert page.locator('#historyJson').count()==0
    page.evaluate('window.scrollTo(0,0)')
    Path('.runtime').mkdir(exist_ok=True);page.screenshot(path='.runtime/web-plan.png',full_page=True)
    page.locator('nav').get_by_role('button',name='Отчёты',exact=True).click();page.get_by_role('heading',name='Сохранённые результаты и экспорт').wait_for()
    page.get_by_role('button',name='Открыть план',exact=True).click();page.locator('#savedResult h2').wait_for()
    # Проверяем переходы по адресу и истории, не создавая проекты и планы в базе.
    project_id='00000000-0000-0000-0000-000000000001'
    suite_id='00000000-0000-0000-0000-000000000002'
    project={'id':project_id,'name':'Проверка навигации','description':'','upload_count':0,'size_bytes':0,'uploads':[],'analyses':[]}
    suite={'suite_id':suite_id,'target_version':'Навигация','status':'draft','provider':'demo','synthetic':True,
           'suggestion':{'proposals':[],'selected_test_ids':[],'summary':'','warnings':[]},
           'affected_objects':[],'warnings':[]}
    counts={'project':0,'suite':0}
    def project_response(route):
        counts['project']+=1
        route.fulfill(json=project)
    def suite_response(route):
        counts['suite']+=1
        route.fulfill(json=suite)
    page.route('**/api/v1/projects/'+project_id,project_response)
    page.route('**/api/v1/ai/suites/'+suite_id,suite_response)
    page.evaluate("(id) => location.hash = '#projects/' + id",project_id)
    page.get_by_role('heading',name='Проверка навигации',exact=True).wait_for()
    page.evaluate("(id) => location.hash = '#ai/' + id",suite_id)
    page.get_by_role('heading',name='План проверок · Навигация',exact=True).wait_for()
    page.go_back()
    page.get_by_role('heading',name='Проверка навигации',exact=True).wait_for()
    page.go_forward()
    page.get_by_role('heading',name='План проверок · Навигация',exact=True).wait_for()
    assert counts=={'project':2,'suite':2},counts
    assert not errors,errors
    assert page.locator('#message').inner_text()==''
    browser.close()
    print('Chromium: 8 разделов, демо, сравнение, граф/пути, HTTP-доставка, каталог, история, план, подтверждение, задание, отчёты — OK; ошибок JavaScript нет.')
