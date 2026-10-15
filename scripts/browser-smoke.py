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
    assert not errors,errors
    assert page.locator('#message').inner_text()==''
    browser.close()
    print('Chromium: 8 разделов, демо, сравнение, граф/пути, HTTP-доставка, каталог, история, план, подтверждение, задание, отчёты — OK; ошибок JavaScript нет.')
