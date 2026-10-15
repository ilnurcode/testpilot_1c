import httpx
import pytest
from testpilot.integration.probe import Profile,probe,parse_metadata,MAX_BYTES

# Эталонный минимальный EDMX OData, а не выгрузка реальной 1С.
METADATA=b'''<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">
<edmx:DataServices><Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
<EntityContainer Name="Default"><EntitySet Name="Catalog_Items" EntityType="StandardODATA.Catalog_Items"/>
</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'''


def test_real_http_adapter_readonly_no_credential_forwarding():
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,content=METADATA if request.url.path.endswith('$metadata') else b'<html>Login</html>')
    p=Profile('https://odata.example.test/base/odata/standard.odata/','https://web.example.test/base/',username='tester',password='unit-test-only')
    assert 'unit-test-only' not in repr(p)
    result=probe(p,transport=httpx.MockTransport(respond))
    assert [r.method for r in requests]==['GET','GET']
    assert requests[0].url.path=='/base/odata/standard.odata/$metadata'
    assert 'authorization' in requests[0].headers and 'authorization' not in requests[1].headers
    assert result['odata']['entity_sets']==[{'name':'Catalog_Items','entity_type':'StandardODATA.Catalog_Items'}]
    assert not result['web']['login_verified'] and not result['code_changes_detected'] and result['tests_executed']==0


@pytest.mark.parametrize('status,expected',[(401,'authentication_required'),(403,'access_denied'),(404,'http_error'),(302,'http_error')])
def test_no_redirects_or_false_success(status,expected):
    seen=[]
    def respond(r):seen.append(r);return httpx.Response(status,headers={'Location':'https://other.example.test'})
    result=probe(Profile('https://odata.example.test'),httpx.MockTransport(respond))
    assert len(seen)==1 and result['odata']['status']==expected


@pytest.mark.parametrize('content',[b'<html>Login</html>',b'not xml',b'<!DOCTYPE x><x/>',METADATA.decode().encode('utf-16'),b'x'*(MAX_BYTES+1)])
def test_invalid_or_unsafe_metadata(content):
    with pytest.raises(ValueError):parse_metadata(content)


def test_network_error_and_invalid_xml_are_not_success():
    def failure(r):raise httpx.ConnectError('unit-test-only',request=r)
    assert probe(Profile('https://odata.example.test'),httpx.MockTransport(failure))['odata']['status']=='connection_error'
    result=probe(Profile('https://odata.example.test'),httpx.MockTransport(lambda r:httpx.Response(200,text='<html>login</html>')))
    assert result['odata']['status']=='invalid_metadata'


@pytest.mark.parametrize('url',['file:///tmp/test','https://user:password@example.test','https://example.test?token=secret','https://example.test#fragment','https://example.test/../x'])
def test_profile_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):Profile(url)


def test_auth_modes_are_explicit():
    with pytest.raises(ValueError):Profile('https://example.test',username='tester')
    with pytest.raises(ValueError):Profile('https://example.test',username='tester',password='unit',authorization='Basic unit')
