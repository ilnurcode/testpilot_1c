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
        assert request.url.path.endswith('/Catalog_Отсутствует')
        return httpx.Response(404)

    client = ODataClient(PROFILE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(odata, 'ODataClient', lambda profile: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Проверка сущности', 'engine': 'odata',
            'cases': [{'entity': 'Catalog_Отсутствует', 'expected': 'доступна'}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'failed'
    assert 'Catalog_Отсутствует' in results[0]['message']
    assert '404' in results[0]['message']
    assert 'actual' not in results[0]
    assert evidence == []
    assert requests == ['/trade/odata/standard.odata/Catalog_Отсутствует']


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
    monkeypatch.setattr(odata, 'ODataClient', lambda profile: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Доступность OData $metadata', 'engine': 'odata',
            'cases': [{'check': 'metadata', 'expected': 'HTTP 200 OK'}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'passed'
    assert results[0]['actual'] == 1
    assert 'EntitySet — 1' in results[0]['message']
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
    monkeypatch.setattr(odata, 'ODataClient', lambda profile: client)
    suite = {
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{
            'title': 'Доступность OData $metadata', 'engine': 'odata',
            'cases': [{'check': 'metadata', 'expected': 'HTTP 200 OK'}],
        }]},
    }

    results, evidence = execute_1c(suite, PROFILE)

    assert len(results) == 1
    assert results[0]['status'] == 'passed'
    assert results[0]['actual'] == 0
    assert results[0]['message'] == 'Сервис OData доступен, но не публикует ни одной сущности — чтение данных не проверено'
    assert evidence == []
    assert requests == ['/trade/odata/standard.odata/$metadata']
