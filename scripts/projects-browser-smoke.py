"""Проверка библиотеки файлов и UI; исключительно синтетические данные, без 1С."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright
from testpilot.demo import configurations

stamp=uuid4().hex[:8];project_name='Продажи — проверка UI '+stamp
base,target=configurations()
for model in (base,target):model['configuration_id']='ui-library-'+stamp
raw1=json.dumps(base,ensure_ascii=False).encode();raw2=json.dumps(target,ensure_ascii=False).encode()
url=os.environ.get('WEB_URL','http://127.0.0.1:8000')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(http_credentials={'username':'expert','password':os.environ['TP_EXPERT_PASSWORD']},viewport={'width':1440,'height':1080},locale='ru-RU')
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def idle():page.wait_for_function("!document.querySelector('#nav button').disabled && document.querySelector('#message').textContent===''")
    page.goto(url+'/#projects');page.get_by_role('heading',name='Проекты и файлы',exact=True).wait_for();idle()
    page.get_by_role('button',name='＋ Новый проект',exact=True).click();page.locator('#projectName').fill(project_name)
    page.locator('#projectDescription').fill('Синтетический проект проверки сохранности файлов и режимов анализа. Не реальная 1С.')
    page.get_by_role('button',name='Создать проект',exact=True).click();page.get_by_role('heading',name=project_name,exact=True).wait_for();idle()
    project_url=page.url
    def upload(content,filename,label):
        page.locator('#uploadFile').set_input_files({'name':filename,'mimeType':'application/octet-stream','buffer':content})
        page.locator('#uploadLabel').fill(label)
        page.get_by_role('button',name='Сохранить файл',exact=True).click();idle()
        page.locator('#uploadFeedback').wait_for()
    upload(raw1,'Разработка-v1.json','1.0')
    assert 'Файл сохранён' in page.locator('#uploadFeedback').inner_text()
    page.get_by_role('button',name='Рассчитать область влияния',exact=True).click();idle()
    page.get_by_role('heading',name='Полный анализ разработки',exact=True).wait_for()
    assert '12' in page.locator('#projectAnalysisReport .analysis-summary').inner_text()
    upload(raw2,'Разработка-v2.json','2.0')
    assert page.locator('#analysisBaseline').input_value()
    page.get_by_role('button',name='Рассчитать область влияния',exact=True).click();idle()
    page.get_by_role('heading',name='Анализ изменений',exact=True).wait_for()
    stats=page.locator('#projectAnalysisReport .analysis-summary strong').all_inner_texts();assert stats[0:2]==['5','10'],stats
    upload(raw2,'Разработка-v2.json','2.0')
    assert 'Дубликат не создан' in page.locator('#uploadFeedback').inner_text()
    assert page.locator('.inspect-upload').count()==2
    page.reload();page.get_by_role('heading',name=project_name,exact=True).wait_for();idle()
    assert page.locator('.inspect-upload').count()==2
    with page.expect_download() as d:
        page.get_by_role('link',name='Скачать Разработка-v2.json',exact=True).click()
    download=d.value;assert hashlib.sha256(Path(download.path()).read_bytes()).hexdigest()==hashlib.sha256(raw2).hexdigest()
    upload(b'synthetic opaque bytes, not a valid 1C extension','Не разобран.cfe','Ожидает адаптера')
    assert 'ещё не' in page.locator('#uploadAnalysis').inner_text() or 'нужен' in page.locator('#uploadAnalysis').inner_text().lower()
    assert page.locator('#uploadAnalysis').get_by_role('button',name='Рассчитать область влияния',exact=True).count()==0
    page.get_by_role('button',name='Выбрать',exact=True).first.click();idle()
    page.get_by_role('button',name='Рассчитать область влияния',exact=True).click();idle()
    page.evaluate('window.scrollTo(0,0)');Path('.runtime').mkdir(exist_ok=True)
    page.screenshot(path='.runtime/projects-desktop.png',full_page=True)
    assert page.locator('.saved-upload-analysis').count()==3
    page.reload();page.get_by_role('heading',name=project_name,exact=True).wait_for();idle()
    assert page.locator('.inspect-upload').count()==3 and page.locator('.saved-upload-analysis').count()==3
    # Узкий экран: страница не должна иметь горизонтальный скролл, таблицы прокручиваются внутри карточки.
    page.set_viewport_size({'width':390,'height':844});page.evaluate('window.scrollTo(0,0)')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
    page.screenshot(path='.runtime/projects-mobile.png',full_page=True)
    page.get_by_role('button',name='← Все проекты',exact=True).click();idle()
    page.locator('#projectSearch').fill(stamp);assert page.locator('.project-card:visible').count()==1
    page.locator('#projectSearch').fill('does-not-exist-'+stamp);assert page.locator('.project-card:visible').count()==0
    assert page.locator('#noProjectMatches').is_visible()
    assert not errors,errors
    Path('.runtime/projects-smoke-result.json').write_text(json.dumps({'project_url':project_url,'project_name':project_name,'uploads':3,'analyses':3,'synthetic':True,'checks':'create/upload/full/delta/duplicate/reload/download/unparsed/mobile/search'},ensure_ascii=False))
    browser.close()
    print('Chromium: проект, загрузки, полный/изменённый анализ, дубликаты, перезагрузка, скачивание, необработанный CFE, поиск и мобильный экран — OK.')
