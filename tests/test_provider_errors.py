"""Типизированные ошибки провайдера нейросети на fake transport.

Сеть и реальный провайдер не используются: проверяется только то, что видит
потребитель — код ошибки, русский текст, отсутствие секретов и отсутствие
отката на локальный или демонстрационный генератор.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from testpilot.ai import provider
from testpilot.impact.algorithms import analyze
from testpilot.web.presentation import sample


def package():
    """Детерминированный синтетический пакет зоны влияния (без БД и сети)."""
    return analyze(
        sample(1),
        sample(2),
        {'edge_types': ['calls', 'uses', 'belongs_to', 'subscribes_to'], 'direction': 'impact', 'max_depth': 10},
    )[1]


def settings(monkeypatch, key='unit-private-key'):
    monkeypatch.setenv('TP_LLM_API_KEY', key)
    monkeypatch.setenv('TP_LLM_MODEL', 'unit-model')
    return key


def detail_of(error):
    """detail HTTPException: словарь с message/code (как отдаёт провайдер)."""
    assert isinstance(error.value.detail, dict), error.value.detail
    return error.value.detail


def test_missing_key_is_typed_and_makes_no_request(monkeypatch):
    monkeypatch.delenv('TP_LLM_API_KEY', raising=False)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': []})

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    assert error.value.status_code == 503
    assert detail['code'] == 'missing_key'
    assert 'ключ' in detail['message'].lower()
    assert seen == [], 'при отсутствии ключа запрос к провайдеру не отправляется'


def test_rejected_credentials_do_not_leak_key_or_upstream_body(monkeypatch):
    key = settings(monkeypatch)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(401, text=json.dumps({'error': {'message': f'UPSTREAM-BODY-MARKER {key}'}}))

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    assert error.value.status_code == 502
    assert detail['code'] == 'rejected_credentials'
    assert detail['upstream_status'] == 401
    assert 'ключ нейросети' in detail['message']
    assert key not in json.dumps(detail, ensure_ascii=False)
    assert 'UPSTREAM-BODY-MARKER' not in json.dumps(detail, ensure_ascii=False), 'тело ответа провайдера не попадает в ошибку'
    assert len(seen) == 1, 'ошибка авторизации не повторяется автоматически'


def test_rate_limit_reports_safe_retry_after_without_auto_retry(monkeypatch):
    settings(monkeypatch)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(429, headers={'Retry-After': '12'}, text='{"error":"slow down"}')

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    assert detail['code'] == 'rate_limited'
    assert detail['retry_after'] == 12
    assert error.value.headers and error.value.headers['Retry-After'] == '12'
    assert len(seen) == 1, '429 не повторяется автоматически'


def test_retry_after_is_clamped_and_bad_values_ignored(monkeypatch):
    settings(monkeypatch)
    for header, expected in (('999999', provider.RETRY_AFTER_LIMIT), ('later', None), ('', None)):
        def respond(request, header=header):
            return httpx.Response(429, headers={'Retry-After': header} if header else {}, text='x')

        with pytest.raises(HTTPException) as error:
            provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))
        assert detail_of(error).get('retry_after') == expected, header


def test_timeout_is_typed_and_not_retried(monkeypatch):
    settings(monkeypatch)
    seen = []

    def respond(request):
        seen.append(request)
        raise httpx.ReadTimeout('timed out', request=request)

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    assert error.value.status_code == 503
    assert detail['code'] == 'timeout'
    assert 'время ожидания' in detail['message']
    assert len(seen) == 1


def test_network_error_is_typed(monkeypatch):
    settings(monkeypatch)

    def respond(request):
        raise httpx.ConnectError('no route', request=request)

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))
    assert detail_of(error)['code'] == 'network_error'


def test_invalid_json_does_not_echo_response_content(monkeypatch):
    key = settings(monkeypatch)

    def respond(request):
        return httpx.Response(200, text='not-json echo=' + key + ' TP_EXPERT_PASSWORD=s3cret tail')

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    serialized = json.dumps(detail, ensure_ascii=False)
    assert detail['code'] == 'invalid_json'
    assert error.value.status_code == 502
    assert 'не JSON' in detail['message']
    for leaked in (key, 'not-json', 's3cret', 'TP_EXPERT_PASSWORD'):
        assert leaked not in serialized, leaked



def test_schema_violation_retries_once_then_reports_contract_error(monkeypatch):
    settings(monkeypatch)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"summary":"x","proposals":[]}'}}]})

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))

    detail = detail_of(error)
    assert detail['code'] == 'invalid_contract'
    assert error.value.status_code == 502
    assert len(seen) == 2, 'одна попытка исправления схемы, затем явная ошибка'


def test_missing_choices_is_contract_error_without_fallback(monkeypatch):
    settings(monkeypatch)

    def respond(request):
        return httpx.Response(200, json={'choices': []})

    with pytest.raises(HTTPException) as error:
        provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))
    detail = detail_of(error)
    assert detail['code'] == 'invalid_contract'
    assert 'не соответствует контракту' in detail['message']


def test_valid_response_still_returns_suggestion(monkeypatch):
    settings(monkeypatch)
    expected = provider.demo_suggestion(package())
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': expected.model_dump_json()}}]})

    result, model = provider.generate(package(), [], {}, transport=httpx.MockTransport(respond))
    assert result.summary == expected.summary
    assert model == 'unit-model'
    assert len(seen) == 1


def test_detail_contains_only_safe_fields():
    detail = provider.ProviderError(
        'rejected_credentials', 'Провайдер отклонил ключ нейросети.', status=502, upstream_status=401, retry_after=5
    ).to_detail()
    assert set(detail) == {'message', 'code', 'upstream_status', 'retry_after'}
    assert detail['message'] == 'Провайдер отклонил ключ нейросети.'
