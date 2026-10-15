"""Проверка презентационного пользовательского пути в Chromium, без реальной 1С и LLM."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=os.environ.get('TP_CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    ctx=b.new_context(http_credentials={'username':'expert','password':os.environ['TP_EXPERT_PASSWORD']},viewport={'width':1440,'height':1050},locale='ru-RU')
    page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def idle():page.wait_for_function("!document.querySelector('#nav button').disabled && document.querySelector('#message').textContent===''")
    page.goto('http://127.0.0.1:8000');page.get_by_role('button',name='Открыть демонстрацию',exact=True).click();idle()
    page.get_by_role('heading',name='Анализ изменений',exact=True).wait_for()
    page.get_by_role('button',name='Подготовить проверки',exact=True).click();idle()
    page.get_by_role('button',name='Сформировать сценарии',exact=True).click();idle()
    page.get_by_role('heading',name='Нейросеть и проверки',exact=True).wait_for()
    assert page.locator('.proposal-card').count()==2
    page.locator('#suiteReason').fill('Проверены сценарии, данные и ожидаемые суммы для учебного стенда')
    page.get_by_role('button',name='Подтвердить выбранные сценарии',exact=True).click();idle()
    page.get_by_role('button',name='Запустить на учебном стенде',exact=True).click();idle()
    page.get_by_role('heading',name='Фактические результаты',exact=True).wait_for()
    assert page.locator('.result-passed').count()==2
    assert page.locator('.result-failed').count()==2
    assert page.locator('.result-card').count()==4
    assert page.locator('.evidence-image').count()==2
    page.get_by_text('Скриншот выполнения',exact=True).first.click()
    page.locator('.evidence-image').first.wait_for()
    page.wait_for_function("document.querySelector('.evidence-image').complete && document.querySelector('.evidence-image').naturalWidth>0")
    page.locator('#suiteView').scroll_into_view_if_needed()
    Path('.runtime').mkdir(exist_ok=True);page.screenshot(path='.runtime/presentation-report.png',full_page=True)
    page.reload();idle();page.get_by_role('heading',name='Фактические результаты',exact=True).wait_for()
    assert page.locator('.result-card').count()==4
    page.set_viewport_size({'width':390,'height':844});page.evaluate('window.scrollTo(0,0)')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path='.runtime/presentation-mobile.png',full_page=True)
    assert not errors,errors
    b.close();print('Презентационный UI: подготовка → сценарии → подтверждение → HTTP/Playwright → 2 passed / 2 failed → скриншоты → восстановление отчёта → мобильная ширина — OK.')
