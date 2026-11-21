"""Сгенерированный план: утверждения проверяются как потребительский контракт.

Проверяется результат `generate_local_1c_tests` и путь LLM на fake transport:
валидность типизированных утверждений, явный scope, отсутствие выдуманного
бизнес-ожидания, передача контракта в контекст модели и отсутствие секретов в
prompt. Сеть и реальный провайдер не используются.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from testpilot.ai import provider
from testpilot.ai.models import Suggestion


def package():
    """Пакет с документом: без БД и сети, как в остальных потребительских тестах плана."""
    return {
        'affected_objects': ['document.заказклиента'],
        'changed_objects': ['document.заказклиента'],
        'configuration_id': 'demo',
        'target_version': '1.0.1',
        'dependency_paths': [],
        'warnings': [],
        'synthetic': False,
    }


def names():
    return {'document.заказклиента': 'Документ.ЗаказКлиента'}


def local_plan():
    return provider.generate_local_1c_tests(package(), [], names())


def assertions_of(suggestion, engine):
    return [assertion for proposal in suggestion.proposals if proposal.engine == engine
            for case in proposal.cases for assertion in case.assertions]


def test_auto_cases_carry_only_typed_probes():
    suggestion = local_plan()
    auto = [proposal for proposal in suggestion.proposals if proposal.engine in {'playwright', 'odata'}]
    assert auto, 'локальный план обязан содержать автоматические probes'
    for proposal in auto:
        assert proposal.cases, proposal.title
        for case in proposal.cases:
            assert case.assertions, f'авто-кейс без утверждений: {proposal.title}'
            for assertion in case.assertions:
                assert assertion.scope in {'connectivity', 'metadata'}, assertion.scope
                assert assertion.required is True
                assert assertion.expected is not None and assertion.expected.type == 'boolean'
                assert assertion.expected.value is True


def test_metadata_probe_matches_b3_contract():
    odata = [proposal for proposal in local_plan().proposals if proposal.engine == 'odata']
    assert len(odata) == 1
    probe = odata[0].cases[0].assertions[0]
    assert (probe.scope, probe.check, probe.engine, probe.operator) == (
        'metadata', 'metadata_entity', 'odata', 'exists'
    )
    assert probe.id and probe.selector.entity == 'Document_ЗаказКлиента'
    assert probe.expected.type == 'boolean' and probe.expected.value is True
    assert probe.source_ref == 'metadata $metadata'


def test_web_section_is_not_invented_without_metadata_evidence():
    """Раздел веб-клиента не выдумывается: у разных конфигураций он разный."""
    suggestion = local_plan()
    assert not [proposal for proposal in suggestion.proposals if proposal.engine == 'playwright']
    assertions = [assertion for proposal in suggestion.proposals for case in proposal.cases
                  for assertion in case.assertions]
    assert 'connectivity' not in {assertion.scope for assertion in assertions}
    manual = [assertion for assertion in assertions if assertion.scope == 'manual'][0]
    assert 'раздел' in manual.requirement.lower()
    assert 'не указан в метаданных' in manual.requirement


def test_web_probe_uses_section_from_metadata_when_present():
    """Улика раздела в метаданных проекта даёт connectivity-probe с этим разделом."""
    with_section = dict(package(), dependency_paths=['CommonModule.ПродажиСервер'])
    suggestion = provider.generate_local_1c_tests(with_section, [], names())
    web = [proposal for proposal in suggestion.proposals if proposal.engine == 'playwright']
    assert len(web) == 1
    probe = web[0].cases[0].assertions[0]
    assert (probe.scope, probe.check, probe.engine, probe.operator) == (
        'connectivity', 'web_section_visible', 'playwright', 'exists'
    )
    assert probe.selector.section == 'Продажи'
    assert probe.expected.type == 'boolean' and probe.expected.value is True
    assert 'метаданных' in (probe.source_ref or '')


def test_business_expectation_is_not_invented_without_requirements():
    suggestion = local_plan()
    scopes = {assertion.scope for proposal in suggestion.proposals for case in proposal.cases
              for assertion in case.assertions}
    assert 'business' not in scopes, 'без требований бизнес-утверждение было бы выдумкой'
    assert any('нет требований' in warning.lower() for warning in suggestion.warnings)
    assert any('не означает успех бизнес' in warning.lower() for warning in suggestion.warnings)


def test_manual_question_is_offered_instead_of_business_result():
    suggestion = local_plan()
    manual = [proposal for proposal in suggestion.proposals if proposal.engine == 'manual']
    assert len(manual) == 1, 'ровно одно ручное предложение с вопросом'
    assertion = manual[0].cases[0].assertions[0]
    assert (assertion.scope, assertion.check, assertion.engine, assertion.operator) == (
        'manual', 'manual', 'manual', 'manual'
    )
    assert assertion.expected is None
    assert assertion.requirement and '?' in assertion.requirement
    assert assertion.source_ref


def test_plan_for_objects_without_documents_stays_probe_only():
    suggestion = provider.generate_local_1c_tests(
        {'affected_objects': ['catalog.номенклатура', 'common_module.общиймодуль']}, [], {}
    )
    scopes = {assertion.scope for proposal in suggestion.proposals for case in proposal.cases
              for assertion in case.assertions}
    assert scopes == {'metadata', 'manual'}
    odata = [proposal for proposal in suggestion.proposals if proposal.engine == 'odata'][0]
    assert odata.cases[0].assertions[0].selector.entity == 'Catalog_Номенклатура'




def test_llm_plan_accepts_probes_and_manual_answer(monkeypatch):
    monkeypatch.setenv('TP_LLM_API_KEY', 'unit-private-key')
    monkeypatch.setenv('TP_LLM_MODEL', 'unit-model')
    expected = local_plan().model_dump_json()
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': expected}}]})

    result, _ = provider.generate(package(), [], names(), transport=httpx.MockTransport(respond))
    assert len(seen) == 1
    scopes = {assertion.scope for proposal in result.proposals for case in proposal.cases
              for assertion in case.assertions}
    assert scopes == {'metadata', 'manual'}


def llm_answer_with_business(monkeypatch, payload, model_context=None):
    monkeypatch.setenv('TP_LLM_API_KEY', 'unit-private-key')
    monkeypatch.setenv('TP_LLM_MODEL', 'unit-model')

    def respond(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)}}]})

    return provider.generate(package(), [], names(), model_context=model_context, transport=httpx.MockTransport(respond))


def business_payload(source_ref=None):
    payload = local_plan().model_dump()
    assertion = {
        'id': 'biz-1', 'scope': 'business', 'check': 'odata_record_count', 'engine': 'odata',
        'operator': 'count_equals', 'selector': {'entity': 'Document_ЗаказКлиента'},
        'expected': {'type': 'integer', 'value': 1},
    }
    if source_ref is not None:
        assertion['source_ref'] = source_ref
    proposal = next(item for item in payload['proposals'] if item['engine'] == 'odata')
    proposal['cases'][0]['assertions'].append(assertion)
    return payload


def test_hallucinated_requirement_reference_is_rejected(monkeypatch):
    """source_ref строкой («ТЗ п.1») не доказывает существование требования."""
    with pytest.raises(HTTPException) as error:
        llm_answer_with_business(monkeypatch, business_payload('ТЗ п.1'))
    assert error.value.status_code == 502
    assert error.value.detail['code'] == 'invalid_contract'
    assert 'нет требований' in error.value.detail['message']


def test_business_assertion_without_reference_is_rejected(monkeypatch):
    monkeypatch.setenv('TP_LLM_API_KEY', 'unit-private-key')
    monkeypatch.setenv('TP_LLM_MODEL', 'unit-model')
    with pytest.raises(HTTPException) as error:
        llm_answer_with_business(monkeypatch, business_payload())
    assert error.value.status_code == 502
    assert error.value.detail['code'] == 'invalid_contract'


def test_llm_context_carries_assertion_contract_without_secrets(monkeypatch):
    key = 'unit-private-key'
    monkeypatch.setenv('TP_LLM_API_KEY', key)
    monkeypatch.setenv('TP_LLM_MODEL', 'unit-model')
    captured = {}

    def respond(request):
        captured['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': local_plan().model_dump_json()}}]})

    provider.generate(package(), [], names(), transport=httpx.MockTransport(respond))
    body = captured['body']
    system = body['messages'][0]['content']
    user = body['messages'][1]['content']
    assert '"assertions"' in system, 'контекст модели обязан содержать поле утверждений'
    for marker in ('metadata_entity', 'web_section_visible', 'business', 'manual'):
        assert marker in system, marker
    assert key not in system and key not in user, 'ключ не попадает в prompt'
    assert key not in json.dumps(body.get('model', ''), ensure_ascii=False)


def test_business_assertion_grounded_in_passed_requirement_is_accepted(monkeypatch):
    """Будущий путь: source_ref обязан совпасть с реально переданным идентификатором."""
    result, _ = llm_answer_with_business(
        monkeypatch,
        business_payload('REQ-1'),
        model_context=[{'requirement_id': 'REQ-1', 'requirement': 'Сумма заказа = 1250.50'}],
    )
    scopes = {assertion.scope for proposal in result.proposals for case in proposal.cases
              for assertion in case.assertions}
    assert 'business' in scopes


def test_business_assertion_with_wrong_requirement_id_is_rejected(monkeypatch):
    with pytest.raises(HTTPException) as error:
        llm_answer_with_business(
            monkeypatch,
            business_payload('REQ-2'),
            model_context=[{'requirement_id': 'REQ-1', 'requirement': 'Сумма заказа = 1250.50'}],
        )
    assert error.value.detail['code'] == 'invalid_contract'




def test_metadata_object_id_is_not_a_requirement_source(monkeypatch):
    """id объекта метаданных или простое поле id не могут быть источником бизнес-ожидания."""
    with pytest.raises(HTTPException) as error:
        llm_answer_with_business(
            monkeypatch,
            business_payload('document.заказклиента'),
            model_context=[{'id': 'document.заказклиента', 'name': 'Заказ клиента'}],
        )
    assert error.value.status_code == 502
    assert error.value.detail['code'] == 'invalid_contract'
    assert 'нет требований' in error.value.detail['message']


def test_requirement_id_without_text_is_not_grounding(monkeypatch):
    """Идентификатор требования без текста тоже не доказывает требование."""
    with pytest.raises(HTTPException) as error:
        llm_answer_with_business(
            monkeypatch, business_payload('REQ-1'), model_context=[{'requirement_id': 'REQ-1'}]
        )
    assert error.value.detail['code'] == 'invalid_contract'
