"""Порядок шагов локального плана: сначала навигация (click), затем ожидание, затем проверка.

Без БД и сети: генератор вызывается напрямую на синтетическом пакете.
"""
from __future__ import annotations

from testpilot.ai.provider import generate_local_1c_tests

PACKAGE = {
    'affected_objects': ['document.заказклиента'],
    'changed_objects': ['document.заказклиента'],
    'configuration_id': 'demo',
    'target_version': '1.0.1',
    # Раздел веб-клиента берётся только из улики: без него web-проба не строится (см. test_local_plan_assertions).
    'dependency_paths': [{'affected_object': 'document.заказклиента',
                          'path': ['Подсистема.Продажи', 'Документ.ЗаказКлиента']}],
    'object_names': {},
}


def _playwright_case():
    suggestion = generate_local_1c_tests(PACKAGE, [], {'document.заказклиента': 'Документ.ЗаказКлиента'})
    proposal = next(item for item in suggestion.proposals if item.engine == 'playwright')
    assert proposal.cases, 'у playwright-предложения нет кейсов'
    return proposal.cases[0].steps


def test_first_step_is_navigation_not_assertion():
    steps = _playwright_case()

    assert steps, 'кейс без шагов'
    assert steps[0]['action'] == 'click', f'первым шагом должна быть навигация, а не {steps[0]["action"]}'
    assert steps[0]['action'] not in {'assert_text', 'assert_section'}


def test_assertion_goes_after_navigation_and_wait():
    steps = _playwright_case()
    actions = [step['action'] for step in steps]

    click_index = actions.index('click')
    assertion_indexes = [i for i, action in enumerate(actions) if action in {'assert_text', 'assert_section'}]
    assert assertion_indexes, 'в кейсе нет проверки assert_*'
    assert min(assertion_indexes) > click_index, f'проверка идёт до навигации: {actions}'

    wait_indexes = [i for i, action in enumerate(actions) if action == 'wait']
    if wait_indexes:
        gap = [i for i in wait_indexes if click_index < i < min(assertion_indexes)]
        assert gap, f'wait должен стоять между кликом и проверкой: {actions}'


def test_assert_section_is_preferred_over_assert_text():
    actions = [step['action'] for step in _playwright_case()]

    assert 'assert_section' in actions, f'нужен assert_section, получено: {actions}'
    assert 'assert_text' not in actions, f'assert_text по тексту пункта меню ненадёжен: {actions}'


def test_steps_match_runner_contract():
    steps = _playwright_case()

    assert set(steps[0]) <= {'action', 'target', 'value'}
    assert all(isinstance(step.get('action'), str) for step in steps)
    assert all(step.get('action') in {'click', 'wait', 'assert_section', 'assert_text', 'open_url', 'fill'} for step in steps)


def _local_suggestion():
    return generate_local_1c_tests(PACKAGE, [], {'document.заказклиента': 'Документ.ЗаказКлиента'})


def test_odata_proposal_is_single_and_has_valid_case():
    suggestion = _local_suggestion()
    odata = [item for item in suggestion.proposals if item.engine == 'odata']

    assert len(odata) == 1, f'ожидался ровно один odata-proposal, получено {len(odata)}'
    assert odata[0].cases, 'odata-предложение без кейсов'
    case = odata[0].cases[0]
    assert (case.entity or '').strip() or getattr(case, 'check', None) == 'metadata', \
        'odata-case должен содержать entity или check=metadata'
    assert case.expected, 'odata-case должен содержать expected'


def test_odata_case_uses_document_entity_from_affected_object():
    case = next(item for item in _local_suggestion().proposals if item.engine == 'odata').cases[0]

    assert case.entity == 'Document_ЗаказКлиента'


def _odata_suite(case: dict) -> dict:
    return {
        'approval': {'selected_proposals': [0]},
        'suggestion': {
            'proposals': [{
                'title': 'OData API 1С',
                'engine': 'odata',
                'object_ids': ['document.заказклиента'],
                'steps': ['прочитать коллекцию'],
                'expected_result': 'HTTP 200 OK',
                'cases': [case],
            }],
        },
    }


COUNT_CASE = {
    'entity': 'Document_ЗаказКлиента',
    'expected': 'В коллекции две записи',
    'assertions': [{
        'id': 'probe-count-1', 'scope': 'business', 'check': 'odata_record_count', 'engine': 'odata',
        'operator': 'count_equals', 'selector': {'entity': 'Document_ЗаказКлиента'},
        'expected': {'type': 'integer', 'value': 2},
        'source_ref': 'требование владельца: в коллекции две записи', 'required': True,
    }],
}

METADATA_CASE = {
    'expected': 'Сущность опубликована',
    'assertions': [{
        'id': 'probe-meta-1', 'scope': 'metadata', 'check': 'metadata_entity', 'engine': 'odata',
        'operator': 'exists', 'selector': {'entity': 'Document_ЗаказКлиента'},
        'expected': {'type': 'boolean', 'value': True},
        'source_ref': 'metadata $metadata', 'required': True,
    }],
}


class _Profile:
    odata_url = 'http://127.0.0.1:59999/odata/standard.odata'
    web_url = None


def test_odata_execution_passes_when_entity_readable(monkeypatch):
    from testpilot.ai import executor
    import testpilot.integration.odata as odata_module

    class FakeClient:
        last_base_url = 'http://fake'

        def __init__(self, profile, transport=None, timeout=20.0, should_cancel=None):
            self.profile = profile
            self.should_cancel = should_cancel

        def get_count(self, entity):
            assert entity == 'Document_ЗаказКлиента'
            assert self.should_cancel is None
            return 2

    monkeypatch.setattr(odata_module, 'ODataClient', FakeClient)

    results, evidence = executor.execute_1c(_odata_suite(COUNT_CASE), _Profile())

    assert results[0]['status'] == 'passed'
    assert results[0]['business_success'] is True
    assert results[0]['assertions'][0]['actual'] == {'type': 'integer', 'value': 2}
    assert not evidence


def test_odata_execution_reports_404_entity_as_error(monkeypatch):
    """404 при чтении — не «проверка не прошла», а «проверку не удалось выполнить»."""
    from testpilot.ai import executor
    import testpilot.integration.odata as odata_module

    class FakeClient:
        last_base_url = 'http://fake'

        def __init__(self, profile, transport=None, timeout=20.0, should_cancel=None):
            self.profile = profile
            self.should_cancel = should_cancel

        def get_count(self, entity):
            raise odata_module.NotFoundError(f'HTTP 404 для {entity}')

    monkeypatch.setattr(odata_module, 'ODataClient', FakeClient)
    case = {**COUNT_CASE, 'entity': 'Document_НетТакой',
            'assertions': [{**COUNT_CASE['assertions'][0],
                            'selector': {'entity': 'Document_НетТакой'}}]}

    results, _ = executor.execute_1c(_odata_suite(case), _Profile())

    assert results[0]['status'] == 'error'
    assert results[0]['business_success'] is None
    assert results[0]['assertions'][0]['status'] == 'error'
    assert 'HTTP 404' in results[0]['message']
    assert results[0]['status'] != 'passed'


def test_odata_execution_passes_metadata_check_without_entity(monkeypatch):
    from testpilot.ai import executor
    import testpilot.integration.odata as odata_module

    class FakeClient:
        last_base_url = 'http://fake'

        def __init__(self, profile, transport=None, timeout=20.0, should_cancel=None):
            self.profile = profile
            self.should_cancel = should_cancel

        def get_metadata(self):
            return [{'name': 'Document_ЗаказКлиента'}]

    monkeypatch.setattr(odata_module, 'ODataClient', FakeClient)

    results, _ = executor.execute_1c(_odata_suite(METADATA_CASE), _Profile())

    assert results[0]['status'] == 'passed'
    assert results[0]['business_success'] is None
    assert results[0]['message'] == 'EntitySet присутствует в metadata'


def test_odata_metadata_check_fails_when_entity_absent(monkeypatch):
    from testpilot.ai import executor
    import testpilot.integration.odata as odata_module

    class FakeClient:
        last_base_url = 'http://fake'

        def __init__(self, profile, transport=None, timeout=20.0, should_cancel=None):
            self.profile = profile
            self.should_cancel = should_cancel

        def get_metadata(self):
            return [{'name': 'Catalog_Номенклатура'}]

    monkeypatch.setattr(odata_module, 'ODataClient', FakeClient)

    results, _ = executor.execute_1c(_odata_suite(METADATA_CASE), _Profile())

    assert results[0]['status'] == 'failed'
    assert results[0]['message'] == 'EntitySet не найден в metadata'


def test_case_without_typed_assertions_is_not_executed():
    """Легаси-кейс с одним expected не исполняется: нужен повторный разбор с типизированными проверками."""
    from testpilot.ai import executor

    results, _ = executor.execute_1c(
        _odata_suite({'entity': 'Document_ЗаказКлиента', 'expected': 'HTTP 200 OK'}), _Profile())

    assert results[0]['status'] == 'error'
    assert results[0].get('business_success') is None
    assert 'typed assertions' in results[0]['message']
