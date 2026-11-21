"""Тесты OData-клиента для 1С:Предприятия."""
import pytest
import httpx
from testpilot.integration.probe import Profile
from testpilot.integration.odata import (
    ODataClient,
    ODataError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    UnsafeEntityError
)

PROFILE = Profile(odata_url='https://1c.example.test/trade/odata/standard.odata', username='test_user', password='pwd')


@pytest.fixture(autouse=True)
def explicit_test_targets(monkeypatch):
    monkeypatch.setenv('TP_TARGET_ALLOWLIST', '1c.example.test')
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')

def test_validate_entity_set_enforces_whitelist():
    client = ODataClient(PROFILE)
    assert client.validate_entity_set('Catalog_Товары') == 'Catalog_Товары'
    assert client.validate_entity_set('Document_Заказ') == 'Document_Заказ'
    assert client.validate_entity_set('InformationRegister_Цены') == 'InformationRegister_Цены'

    with pytest.raises(UnsafeEntityError):
        client.validate_entity_set('Users')
    with pytest.raises(UnsafeEntityError):
        client.validate_entity_set('Catalog_Товары/../hack')
    with pytest.raises(UnsafeEntityError):
        client.validate_entity_set('System_Config')

def test_get_entities_mock():
    items = [{'Ref_Key': '111', 'Description': 'Товар 1'}, {'Ref_Key': '222', 'Description': 'Товар 2'}]
    def handler(req: httpx.Request):
        assert req.url.path == '/trade/odata/standard.odata/Catalog_Товары'
        assert req.url.params['$top'] == '10'
        return httpx.Response(200, json={'value': items})

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    res = client.get_entities('Catalog_Товары', top=10)
    assert len(res) == 2
    assert res[0]['Description'] == 'Товар 1'

def test_get_entity_by_guid():
    guid = '00000000-0000-0000-0000-000000000001'
    def handler(req: httpx.Request):
        assert f"guid'{guid}'" in req.url.path
        return httpx.Response(200, json={'Ref_Key': guid, 'Description': 'Единичный объект'})

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    item = client.get_entity('Catalog_Товары', guid)
    assert item['Ref_Key'] == guid

def test_create_and_delete_entity():
    created = {'Ref_Key': '333', 'Description': 'Новый товар'}
    def handler(req: httpx.Request):
        if req.method == 'POST':
            assert req.headers['content-type'].startswith('application/json')
            return httpx.Response(201, json=created)
        if req.method == 'DELETE':
            return httpx.Response(204)
        return httpx.Response(400)

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    res = client.create_entity('Catalog_Товары', {'Description': 'Новый товар'})
    assert res['Ref_Key'] == '333'

    deleted = client.delete_entity('Catalog_Товары', '333')
    assert deleted is True

def test_error_handling_statuses():
    def make_client(status, payload):
        return ODataClient(PROFILE, transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))

    with pytest.raises(AuthenticationError):
        make_client(401, {}).get_entities('Catalog_Товары')

    with pytest.raises(PermissionDeniedError):
        make_client(403, {}).get_entities('Catalog_Товары')

    with pytest.raises(NotFoundError):
        make_client(404, {}).get_entity('Catalog_Товары', '123')

    with pytest.raises(ODataError, match="Объект заблокирован"):
        make_client(400, {'odata.error': {'message': {'value': 'Объект заблокирован'}}}).get_entities('Catalog_Товары')


def test_executor_fails_for_missing_entity_even_when_metadata_is_available(monkeypatch):
    from testpilot.ai.executor import execute_1c
    import testpilot.integration.odata as odata

    requests = []
    metadata = b'''<?xml version="1.0" encoding="utf-8"?>
    <edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
      <edmx:DataServices />
    </edmx:Edmx>'''

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith('/$metadata'):
            return httpx.Response(200, content=metadata)
        assert request.url.path.endswith("/Catalog_Отсутствует('1')")
        return httpx.Response(404)

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(odata, 'ODataClient', lambda profile, **kwargs: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Проверка сущности', 'engine': 'odata',
            'cases': [{'assertions': [{
                'id': 'missing', 'scope': 'business', 'check': 'odata_field_equals',
                'engine': 'odata', 'operator': 'equals',
                'selector': {'entity': 'Catalog_Отсутствует', 'key': '1', 'field': 'Description'},
                'expected': {'type': 'string', 'value': 'доступна'}, 'source_ref': 'ТЗ',
            }]}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'error'
    assert results[0]['business_success'] is None
    assert '404' in results[0]['message']
    assert results[0]['assertions'][0]['actual'] is None
    assert evidence == []
    assert requests == ["/trade/odata/standard.odata/Catalog_Отсутствует('1')"]


def test_executor_checks_metadata_without_entity(monkeypatch):
    from testpilot.ai.executor import execute_1c
    import testpilot.integration.odata as odata

    requests = []
    metadata = b'''<?xml version="1.0" encoding="utf-8"?>
    <edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
      <edmx:DataServices>
        <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
          <EntityContainer Name="Demo">
            <EntitySet Name="Catalogs" EntityType="Demo.Catalog" />
          </EntityContainer>
        </Schema>
      </edmx:DataServices>
    </edmx:Edmx>'''

    def handler(request):
        requests.append(request.url.path)
        assert request.url.path.endswith('/$metadata')
        return httpx.Response(200, content=metadata)

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(odata, 'ODataClient', lambda profile, **kwargs: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Доступность OData $metadata', 'engine': 'odata',
            'cases': [{'assertions': [{
                'id': 'metadata', 'scope': 'metadata', 'check': 'metadata_entity',
                'engine': 'odata', 'operator': 'exists', 'selector': {'entity': 'Catalogs'},
            }]}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'passed'
    assert results[0]['business_success'] is None
    assert results[0]['assertions'][0]['actual'] == {'type': 'boolean', 'value': True}
    assert evidence == []
    assert requests == ['/trade/odata/standard.odata/$metadata']


def test_executor_reports_empty_metadata_without_claiming_data_check(monkeypatch):
    from testpilot.ai.executor import execute_1c
    import testpilot.integration.odata as odata

    requests = []
    metadata = b'''<?xml version="1.0" encoding="utf-8"?>
    <edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
      <edmx:DataServices />
    </edmx:Edmx>'''

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, content=metadata)

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(odata, 'ODataClient', lambda profile, **kwargs: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Доступность OData $metadata', 'engine': 'odata',
            'cases': [{'assertions': [{
                'id': 'metadata', 'scope': 'metadata', 'check': 'metadata_entity',
                'engine': 'odata', 'operator': 'exists', 'selector': {'entity': 'Catalogs'},
            }]}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'failed'
    assert results[0]['business_success'] is None
    assert results[0]['assertions'][0]['actual'] == {'type': 'boolean', 'value': False}
    assert evidence == []
    assert requests == ['/trade/odata/standard.odata/$metadata']


def test_client_loopback_fallback_reports_the_address_that_answered(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    hosts = []

    def handler(request: httpx.Request):
        hosts.append(request.url.host)
        if request.url.host == 'host.docker.internal':
            raise httpx.ConnectError('mapped host unreachable', request=request)
        return httpx.Response(200, json={'value': [{'Ref_Key': '1'}, {'Ref_Key': '2'}]})

    client = ODataClient(
        Profile(odata_url='http://127.0.0.1:9099/odata/standard.odata', username='tester', password='unit-test-only'),
        transport=httpx.MockTransport(handler),
    )
    assert client.base_url == 'http://host.docker.internal:9099/odata/standard.odata'
    assert len(client.get_entities('Catalog_Номенклатура', top=5)) == 2
    assert hosts == ['host.docker.internal', '127.0.0.1']
    assert client.last_base_url == 'http://127.0.0.1:9099/odata/standard.odata'


def test_client_does_not_retry_http_error_from_mapped_host(monkeypatch):
    import testpilot.integration.probe as probe_module

    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    hosts = []

    def handler(request: httpx.Request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    client = ODataClient(
        Profile(odata_url='http://127.0.0.1:9099/odata/standard.odata'),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NotFoundError):
        client.get_entities('Catalog_Номенклатура', top=5)
    assert hosts == ['host.docker.internal']


def test_actual_send_policy_is_not_bypassed_by_injected_transport(monkeypatch):
    requests = []
    client = ODataClient(PROFILE, transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={'value': []}),
    ))
    monkeypatch.delenv('TP_TARGET_ALLOWLIST')
    with pytest.raises(ODataError, match='не входит'):
        client.get_entities('Catalog_Товары')
    assert requests == []


def test_cancel_before_fallback_prevents_next_actual_send(monkeypatch):
    import testpilot.integration.probe as probe_module
    monkeypatch.setattr(probe_module, 'is_containerized', lambda: True)
    requests = []
    cancelled = [False]

    def handler(request):
        requests.append(request.url.host)
        cancelled[0] = True
        raise httpx.ConnectError('fixture connection refused', request=request)

    client = ODataClient(Profile(odata_url='http://127.0.0.1:9099/odata'),
                         transport=httpx.MockTransport(handler),
                         should_cancel=lambda: cancelled[0])
    with pytest.raises(ODataError, match='Отменено'):
        client.get_entities('Catalog_Товары')
    assert requests == ['host.docker.internal']
