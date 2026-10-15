"""Read-only диагностика публикации 1С; не анализ кода и не исполнение тестов."""
import argparse
import json
import os
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

MAX_BYTES=4*1024*1024
EDMX_NAMESPACES={
    'http://schemas.microsoft.com/ado/2007/06/edmx',
    'http://docs.oasis-open.org/odata/ns/edmx',
}


def validated_url(value):
    parts=urlsplit(value)
    if parts.scheme not in ('http','https') or not parts.hostname:
        raise ValueError('Нужен полный URL публикации http/https')
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('URL не должен содержать учётные данные, query или fragment')
    if any(part in ('.','..') for part in parts.path.split('/')):
        raise ValueError('Недопустимый путь публикации')
    return value.rstrip('/')


@dataclass(frozen=True)
class Profile:
    odata_url: str
    web_url: str | None = None
    username: str | None = None
    password: str | None = field(default=None,repr=False)
    authorization: str | None = field(default=None,repr=False)
    ca_file: str | None = None

    def __post_init__(self):
        validated_url(self.odata_url)
        if self.web_url: validated_url(self.web_url)
        if bool(self.username)!=bool(self.password):
            raise ValueError('Для Basic Auth нужны оба значения TP_1C_USERNAME и TP_1C_PASSWORD')
        if self.authorization and self.username:
            raise ValueError('Выберите один способ аутентификации: Authorization или username/password')
        if self.authorization and ('\r' in self.authorization or '\n' in self.authorization):
            raise ValueError('Некорректный заголовок аутентификации')

    @classmethod
    def from_environment(cls):
        url=os.environ.get('TP_1C_ODATA_URL','')
        if not url:raise ValueError('Задайте TP_1C_ODATA_URL; значения секретов в чат не передавайте')
        return cls(url,os.environ.get('TP_1C_WEB_URL') or None,
                   os.environ.get('TP_1C_USERNAME') or None,os.environ.get('TP_1C_PASSWORD') or None,
                   os.environ.get('TP_1C_AUTHORIZATION') or None,os.environ.get('TP_1C_CA_FILE') or None)


def parse_metadata(content):
    if len(content)>MAX_BYTES:raise ValueError('Документ $metadata превышает 4 МиБ')
    # UTF-16 и DTD не принимаются: не допускаем внешние сущности и expansion.
    if b'\x00' in content or b'<!doctype' in content.lower() or b'<!entity' in content.lower():
        raise ValueError('Нужен UTF-8 XML без DTD и ENTITY')
    try:root=ET.fromstring(content)
    except ET.ParseError:raise ValueError('Ответ не является корректным XML $metadata') from None
    if root.tag not in {f'{{{ns}}}Edmx' for ns in EDMX_NAMESPACES}:
        raise ValueError('Ответ не является OData EDMX; возможно, получена страница входа')
    sets=[]
    for element in root.iter():
        if element.tag.rsplit('}',1)[-1]=='EntitySet':
            name=element.get('Name');entity_type=element.get('EntityType')
            if name and entity_type:sets.append({'name':name,'entity_type':entity_type})
    return sorted(sets,key=lambda x:(x['name'],x['entity_type']))


def read_bounded(client,url,headers=None,auth=None):
    # Redirect не следует автоматически: credentials не переходят на другой адрес.
    with client.stream('GET',url,headers=headers,auth=auth) as response:
        if response.status_code!=200:return response.status_code,b''
        data=bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data)>MAX_BYTES:raise ValueError('Ответ публикации превышает 4 МиБ')
        return response.status_code,bytes(data)


def probe(profile,transport=None):
    headers={'Accept':'application/xml'}
    auth=None
    if profile.authorization:headers['Authorization']=profile.authorization
    elif profile.username:auth=httpx.BasicAuth(profile.username,profile.password)
    verify=ssl.create_default_context(cafile=profile.ca_file) if profile.ca_file else True
    result={'odata':{'status':'not_checked'},'web':{'status':'not_checked'},
            'code_changes_detected':False,'tests_executed':0,
            'warnings':['OData $metadata не содержит исходный BSL-код и не выявляет изменения расширений.',
                        'Доступность веб-страницы не подтверждает успешный вход или совместимость Playwright-сценариев.']}
    with httpx.Client(timeout=15,verify=verify,follow_redirects=False,transport=transport) as client:
        try:
            status,body=read_bounded(client,validated_url(profile.odata_url)+'/$metadata',headers,auth)
            if status==200:
                entities=parse_metadata(body)
                result['odata']={'status':'metadata_available','entity_sets':entities,'entity_set_count':len(entities)}
            else:
                result['odata']={'status':'authentication_required' if status==401 else 'access_denied' if status==403 else 'http_error','http_status':status}
        except httpx.HTTPError:
            result['odata']={'status':'connection_error','message':'Не удалось получить $metadata: проверьте адрес, сеть и цепочку доверия TLS'}
        except ValueError as e:
            result['odata']={'status':'invalid_metadata','message':str(e)}
        if profile.web_url:
            try:
                # Не переносим OData-пароль на публикацию веб-клиента. Вход проверит отдельный сценарий.
                status,_=read_bounded(client,validated_url(profile.web_url))
                result['web']={'status':'reachable' if status==200 else 'authentication_required' if status==401 else 'http_response','http_status':status,'login_verified':False}
            except httpx.HTTPError:
                result['web']={'status':'connection_error','login_verified':False}
            except ValueError as e:
                result['web']={'status':'invalid_response','message':str(e),'login_verified':False}
    return result


def main():
    parser=argparse.ArgumentParser(description='Read-only проверка публикации 1С: только GET $metadata и GET страницы веб-клиента')
    parser.parse_args()
    try:result=probe(Profile.from_environment())
    except (ValueError,OSError):
        print(json.dumps({'error':'Проверьте TP_1C_ODATA_URL, способ аутентификации и TP_1C_CA_FILE; секреты не выводятся'},ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['odata']['status']=='metadata_available' else 1)


if __name__=='__main__':main()
