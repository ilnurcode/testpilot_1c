import hashlib
import io
import json
from copy import deepcopy
from zipfile import ZipFile
from testpilot.demo import configurations


def project(c,name='Тестовый проект'):
    r=c.post('/api/v1/projects',json={'name':name,'description':'Учебная проверка библиотеки файлов'})
    assert r.status_code==200,r.text
    return r.json()['id']


def upload(c,p,content,filename='development.json',**metadata):
    return c.post(f'/api/v1/projects/{p}/uploads',params={'filename':filename,**metadata},content=content,headers={'Content-Type':'application/octet-stream'})


def pack(value):return json.dumps(value,ensure_ascii=False).encode()


def test_persistent_original_files_and_idempotency(clients):
    c,_=clients;p=project(c);raw=b'opaque-format-no-parser'
    r=upload(c,p,raw,'Расширение.cfe',label='1.0');assert r.status_code==200,r.text
    first=r.json();assert first['status']=='requires_adapter' and first['mode']=='full'
    assert first['checksum']==hashlib.sha256(raw).hexdigest()
    assert upload(c,p,raw,'AnotherName.cfe').json()['id']==first['id']
    r=c.get(f'/api/v1/projects/{p}');assert r.json()['upload_count']==1
    assert 'content' not in r.json()['uploads'][0]
    original=c.get(f'/api/v1/projects/{p}/uploads/{first["id"]}/download')
    assert original.content==raw and original.headers['x-content-type-options']=='nosniff'
    assert 'UTF-8' in original.headers['content-disposition']
    # Данные читаются через новое соединение, а не из памяти приложения.
    from testpilot import db
    db.engine().dispose()
    assert c.get(f'/api/v1/projects/{p}').json()['uploads'][0]['id']==first['id']
    blocked=c.post(f'/api/v1/projects/{p}/uploads/{first["id"]}/analyze',json={})
    assert blocked.status_code==409 and 'сохранён' in blocked.text


def test_first_full_then_delta_and_explicit_full(clients):
    c,_=clients;p=project(c);a,b=configurations()
    u1=upload(c,p,pack(a),label='1.0').json()
    r=c.post(f'/api/v1/projects/{p}/uploads/{u1["id"]}/analyze',json={})
    assert r.status_code==200,r.text
    full=r.json();assert full['mode']=='full' and len(full['package']['affected_objects'])==len(a['objects'])
    assert len(full['changes'])==len(a['objects']) and all(x['status']=='added' for x in full['changes'])
    u2=upload(c,p,pack(b),label='2.0').json()
    assert u2['baseline_id']==u1['id'] and u2['mode']=='changes'
    r=c.post(f'/api/v1/projects/{p}/uploads/{u2["id"]}/analyze',json={})
    assert r.status_code==200,r.text
    delta=r.json();assert delta['mode']=='changes'
    assert len(delta['package']['changed_objects'])==5 and len(delta['package']['affected_objects'])==10
    assert c.get('/api/v1/analyses/'+delta['analysis_id']+'/export').json()==delta['package']
    explicit=c.post(f'/api/v1/projects/{p}/uploads/{u2["id"]}/analyze',json={'baseline_id':None}).json()
    assert explicit['mode']=='full' and len(explicit['package']['affected_objects'])==len(b['objects'])
    history=c.get(f'/api/v1/projects/{p}').json()
    assert len(history['analyses'])==3 and history['uploads'][0]['label']=='2.0'
    assert history['upload_count']==2


def test_components_and_projects_do_not_mix_baselines(clients):
    c,_=clients;p=project(c);p2=project(c,'Другой проект')
    u=upload(c,p,b'one','a.cfe',component='Первый').json()
    assert upload(c,p,b'two','b.cfe',component='Второй').json()['baseline_id'] is None
    assert upload(c,p2,b'one','a.cfe',component='Первый').json()['baseline_id'] is None
    assert upload(c,p,b'three','c.cfe',component='Второй',baseline_id=u['id']).status_code==422
    assert c.get(f'/api/v1/projects/{p2}/uploads/{u["id"]}/download').status_code==404


def test_conflicts_and_invalid_upload_are_atomic(clients):
    c,_=clients;p=project(c)
    assert upload(c,p,b'one','a.cfe',label='1.0').status_code==200
    assert upload(c,p,b'two','a.cfe',label='1.0').status_code==409
    assert upload(c,p,b'{invalid}','bad.json').status_code==422
    assert upload(c,p,b'','empty.cfe').status_code==422
    assert upload(c,p,b'text','../escape.cfe').status_code==422
    assert upload(c,p,b'text','script.html').status_code==422
    detail=c.get(f'/api/v1/projects/{p}').json();assert detail['upload_count']==1
    assert upload(c,p,b'three','a.cfe').json()['number']==2


def test_no_silent_version_overwrite_or_cross_project_configuration(clients):
    c,_=clients;p=project(c);p2=project(c,'Другой');a,_=configurations()
    assert upload(c,p,pack(a)).status_code==200
    changed=deepcopy(a);changed['objects'][0]['attributes']['modified']=True
    assert upload(c,p,pack(changed)).status_code==409
    assert upload(c,p2,pack(a)).status_code==409
    assert c.get(f'/api/v1/projects/{p2}').json()['upload_count']==0


def test_zip_normalized_and_source_archive_pending(clients):
    c,_=clients;p=project(c)
    def zipped(files):
        out=io.BytesIO()
        with ZipFile(out,'w') as z:
            for name,body in files.items():z.writestr(name,body)
        return out.getvalue()
    raw=zipped({'configuration.json':pack(configurations()[0]),'readme.txt':'synthetic'})
    u=upload(c,p,raw,'source.zip').json();assert u['status']=='ready' and u['summary']['object_count']==12
    u=upload(c,p,zipped({'Module.bsl':'Процедура Тест()\nКонецПроцедуры'}),'source.zip').json()
    assert u['status']=='requires_adapter'
    assert upload(c,p,zipped({'../escape.txt':'test'}),'unsafe.zip').status_code==422
    assert upload(c,p,b'not a zip','corrupt.zip').status_code==422


def test_cannot_compare_newer_or_unparsed_upload(clients):
    c,_=clients;p=project(c);a,b=configurations()
    raw=upload(c,p,b'pending','a.cfe').json()
    u1=upload(c,p,pack(a)).json();u2=upload(c,p,pack(b)).json()
    assert c.post(f'/api/v1/projects/{p}/uploads/{u1["id"]}/analyze',json={'baseline_id':u2['id']}).status_code==422
    assert c.post(f'/api/v1/projects/{p}/uploads/{u1["id"]}/analyze',json={'baseline_id':raw['id']}).status_code==409


def test_reader_cannot_upload_or_create_project(clients):
    c,_=clients;p=project(c)
    assert c.post('/api/v1/projects',json={'name':'No'},auth=('runner','test-password-only')).status_code==403
    r=c.post(f'/api/v1/projects/{p}/uploads?filename=a.cfe',content=b'test',auth=('runner','test-password-only'))
    assert r.status_code==403
    assert c.get(f'/api/v1/projects/{p}').json()['upload_count']==0


def test_larger_binary_than_json_request_limit_is_saved(clients):
    c,_=clients;p=project(c);raw=b'x'*(8*1024*1024+1)
    r=upload(c,p,raw,'large.cfe');assert r.status_code==200,r.text
    assert r.json()['size_bytes']==len(raw)
    assert c.get(f'/api/v1/projects/{p}/uploads/{r.json()["id"]}/download').content==raw


def test_concurrent_identical_uploads_create_one_file(clients):
    from concurrent.futures import ThreadPoolExecutor
    c,_=clients;p=project(c)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=list(pool.map(lambda _:upload(c,p,b'synthetic-concurrent','file.cfe'),range(3)))
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['id'] for r in responses})==1
    assert sum(not r.json()['idempotent'] for r in responses)==1
    assert c.get(f'/api/v1/projects/{p}').json()['upload_count']==1


def test_invalid_labels_do_not_break_database_transaction(clients):
    c,_=clients;p=project(c)
    assert upload(c,p,b'file','file.cfe',component='bad\x00value').status_code==422
    assert upload(c,p,b'file','file.cfe',label='bad\nvalue').status_code==422
    assert c.get(f'/api/v1/projects/{p}').json()['upload_count']==0
