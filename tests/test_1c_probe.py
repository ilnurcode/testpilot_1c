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


@pytest.mark.parametrize('content',[b'<html>Login</html>',b'not xml',b'<!DOCTYPE x><x/>',METADATA.decode().encode('utf-16'),b'x'*(MAX_BYTES+1)],ids=['html','not_xml','doctype','utf16','oversized'])
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


LOOPBACK = 'http://127.0.0.1:9099/odata/standard.odata'


def test_loopback_mapping_uses_docker_host_when_it_answers(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=METADATA)

    result = probe(Profile(LOOPBACK), httpx.MockTransport(respond))
    assert seen == ['http://host.docker.internal:9099/odata/standard.odata/$metadata']
    assert result['odata']['status'] == 'metadata_available'
    assert result['odata']['url'] == 'http://host.docker.internal:9099/odata/standard.odata/$metadata'


def test_loopback_falls_back_to_original_address_only_on_connect_error(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    seen = []

    def respond(request):
        seen.append(request.url.host)
        if request.url.host == 'host.docker.internal':
            raise httpx.ConnectError('mapped host unreachable', request=request)
        return httpx.Response(200, content=METADATA)

    result = probe(Profile(LOOPBACK), httpx.MockTransport(respond))
    assert seen == ['host.docker.internal', '127.0.0.1']
    assert result['odata']['status'] == 'metadata_available'
    assert result['odata']['url'] == f'{LOOPBACK}/$metadata'


def test_loopback_fallback_does_not_retry_non_network_errors(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    seen = []

    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(401)

    result = probe(Profile(LOOPBACK), httpx.MockTransport(respond))
    assert seen == ['host.docker.internal']
    assert result['odata']['status'] == 'authentication_required'


def test_explicit_host_is_not_rewritten_in_container(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    seen = []

    def respond(request):
        seen.append(request.url.host)
        raise httpx.ConnectError('publication is down', request=request)

    result = probe(Profile('https://odata.example.test/base/odata/standard.odata'), httpx.MockTransport(respond))
    assert seen == ['odata.example.test']
    assert result['odata']['status'] == 'connection_error'


def test_web_only_project_probe_uses_local_fixture_and_not_environment(clients, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'<html>Local probe fixture</html>')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
        monkeypatch.setenv('TP_1C_ODATA_URL', 'https://never-contact.example.test/odata')
        monkeypatch.setenv('TP_1C_AUTHORIZATION', 'Bearer must-not-be-forwarded')
        url = f'http://127.0.0.1:{server.server_port}/web'
        result = probe(Profile(odata_url='', web_url=url))
        assert result['odata'] == {'status': 'not_configured'}
        assert result['web']['status'] == 'reachable'
        assert result['web']['login_verified'] is False
        assert result['tests_executed'] == 0
        impact, _ = clients
        response = impact.post('/api/v1/projects', json={'name': 'Web-only probe', 'description': ''})
        assert response.status_code == 200, response.text
        project = response.json()['id']
        response = impact.put(f'/api/v1/projects/{project}/connection', json={'web_url': url})
        assert response.status_code == 200, response.text
        response = impact.post(f'/api/v1/projects/{project}/connection/probe')
        assert response.status_code == 200, response.text
        assert response.json()['odata'] == {'status': 'not_configured'}
        assert response.json()['web']['status'] == 'reachable'
        assert requests == [('/web', None), ('/web', None)]
        assert impact.put(f'/api/v1/projects/{project}/connection', json={}).status_code == 200
        assert impact.post(f'/api/v1/projects/{project}/connection/probe').status_code == 422
        with pytest.raises(ValueError, match='odata_url или web_url'):
            Profile(odata_url='', web_url=None)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
