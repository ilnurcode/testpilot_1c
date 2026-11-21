from decimal import Decimal

import pytest
from pydantic import ValidationError

from testpilot.ai.models import Assertion
from testpilot.ai.executor import evaluate_assertions


def test_metadata_business_count_playwright_manual_schema():
    metadata = Assertion.model_validate({
        'id': 'meta', 'scope': 'metadata', 'check': 'metadata_entity', 'engine': 'odata',
        'operator': 'exists', 'selector': {'entity': 'Document_Заказ'},
        'expected': {'type': 'boolean', 'value': True},
    })
    assert metadata.scope == 'metadata'
    field = Assertion.model_validate({
        'id': 'field', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
        'operator': 'equals', 'selector': {'entity': 'Document_Заказ', 'key': '1', 'field': 'Сумма'},
        'expected': {'type': 'number', 'value': '1250.50'}, 'source_ref': 'ТЗ: сумма 1250.50',
    })
    assert field.expected.value == Decimal('1250.50')
    count = Assertion.model_validate({
        'id': 'count', 'scope': 'business', 'check': 'odata_record_count', 'engine': 'odata',
        'operator': 'count_equals', 'selector': {'entity': 'Document_Заказ'},
        'expected': {'type': 'integer', 'value': 1}, 'source_ref': 'ТЗ: один заказ',
    })
    assert count.expected.value == 1
    posted = Assertion.model_validate({
        'id': 'posted', 'scope': 'business', 'check': 'odata_posted_state', 'engine': 'odata',
        'operator': 'posted', 'selector': {'entity': 'Document_Заказ', 'key': '1'},
        'expected': {'type': 'boolean', 'value': True}, 'source_ref': 'ТЗ: проведён',
    })
    assert posted.operator == 'posted'
    probe = Assertion.model_validate({
        'id': 'probe', 'scope': 'connectivity', 'check': 'web_section_visible', 'engine': 'playwright',
        'operator': 'exists', 'selector': {'section': 'Продажи'}, 'expected': {'type': 'boolean', 'value': True},
    })
    assert probe.scope == 'connectivity'
    manual = Assertion.model_validate({
        'id': 'manual', 'scope': 'manual', 'check': 'manual', 'engine': 'manual', 'operator': 'manual',
        'requirement': 'Проверить печатную форму', 'source_ref': 'ТЗ: печать',
    })
    assert manual.requirement


@pytest.mark.parametrize('payload', [
    {'id': 'bad', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'browser',
     'operator': 'equals', 'selector': {'entity': 'X', 'key': '1', 'field': 'F'},
     'expected': {'type': 'boolean', 'value': True}, 'source_ref': 'x'},
    {'id': 'bad', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
     'operator': 'equals', 'selector': {'entity': 'X', 'key': '1', 'field': 'F'},
     'expected': {'type': 'boolean', 'value': 1}, 'source_ref': 'x'},
    {'id': 'bad', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
     'operator': 'equals', 'selector': {'entity': 'X', 'key': '1', 'field': 'F'},
     'expected': {'type': 'integer', 'value': True}, 'source_ref': 'x'},
    {'id': 'bad', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
     'operator': 'equals', 'selector': {'entity': 'X', 'key': '1', 'field': 'F'},
     'expected': {'type': 'integer', 'value': 1}},
])
def test_rejects_wrong_types_unknown_engine_and_missing_source(payload):
    with pytest.raises(ValidationError):
        Assertion.model_validate(payload)


class FakeOData:
    def __init__(self):
        self.entity = {'Сумма': 1250.50, 'Posted': True}
    def get_metadata(self):
        return [{'name': 'Document_Заказ'}]
    def get_entities(self, entity, top=1000):
        return [{'id': 1}]
    def get_entity(self, entity, key):
        return self.entity


def test_evaluator_business_vs_probe_and_error():
    assertions = [
        {'id': 'b', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
         'operator': 'equals', 'selector': {'entity': 'Document_Заказ', 'key': '1', 'field': 'Сумма'},
         'expected': {'type': 'number', 'value': '1250.50'}, 'source_ref': 'ТЗ'},
        {'id': 'p', 'scope': 'connectivity', 'check': 'web_section_visible', 'engine': 'playwright',
         'operator': 'exists', 'selector': {'section': 'Продажи'}, 'expected': {'type': 'boolean', 'value': True}},
    ]
    status, business, details = evaluate_assertions(assertions, FakeOData(), None)
    assert status == 'error'
    assert business is None
    assert details[0]['status'] == 'passed'
    assert details[1]['status'] == 'error'

    status, business, details = evaluate_assertions(assertions[:1], FakeOData(), None)
    assert (status, business) == ('passed', True)

    bad = dict(assertions[0], expected={'type': 'number', 'value': '999'})
    status, business, details = evaluate_assertions([bad], FakeOData(), None)
    assert (status, business) == ('failed', False)


class CountOData(FakeOData):
    def __init__(self, count=1001, field='2026-01-01T00:00:00Z'):
        self.count = count
        self.entity = {'Сумма': '1250.50', 'Posted': True, 'Дата': field}
    def get_count(self, entity, filter_query=None):
        return self.count


def test_business_expected_outcomes_and_typed_edges():
    base = {'id': 'b', 'scope': 'business', 'check': 'odata_field_equals', 'engine': 'odata',
            'operator': 'equals', 'selector': {'entity': 'Document_Заказ', 'key': '1', 'field': 'Сумма'},
            'expected': {'type': 'number', 'value': '1250.50'}, 'source_ref': 'ТЗ'}
    assert evaluate_assertions([base], CountOData(), None)[0] == 'passed'
    wrong = dict(base, expected={'type': 'number', 'value': '999'})
    assert evaluate_assertions([wrong], CountOData(), None)[0] == 'failed'

    missing = dict(base, selector={'entity': 'Document_Заказ', 'key': '1', 'field': 'Нет'})
    assert evaluate_assertions([missing], CountOData(), None)[0] == 'failed'
    status, business, details = evaluate_assertions([missing], CountOData(), None)
    assert status == 'failed' and details[0]['actual']['type'] == 'missing'
    null_actual = CountOData()
    null_actual.entity['Сумма'] = None
    status, business, details = evaluate_assertions([base], null_actual, None)
    assert status == 'failed' and details[0]['actual']['type'] == 'null'

    date_ok = dict(base, selector={'entity': 'Document_Заказ', 'key': '1', 'field': 'Дата'},
                   expected={'type': 'date', 'value': '2026-01-01T00:00:00+00:00'})
    assert evaluate_assertions([date_ok], CountOData(), None)[0] == 'passed'

    count = {'id': 'c', 'scope': 'business', 'check': 'odata_record_count', 'engine': 'odata',
             'operator': 'count_equals', 'selector': {'entity': 'Document_Заказ'},
             'expected': {'type': 'integer', 'value': 1001}, 'source_ref': 'ТЗ'}
    assert evaluate_assertions([count], CountOData(1001), None)[0] == 'passed'

    probe = {'id': 'p', 'scope': 'connectivity', 'check': 'web_section_visible', 'engine': 'playwright',
             'operator': 'exists', 'selector': {'section': 'Продажи'},
             'expected': {'type': 'boolean', 'value': False}}
    assert evaluate_assertions([probe], None, None, web_actuals={'p': {'value': False}})[0] == 'passed'
    assert evaluate_assertions([probe], None, None, web_actuals={'p': {'value': True}})[0] == 'failed'
    assert evaluate_assertions(
        [probe], None, None, web_actuals={'p': {'status': 'error', 'value': None, 'error': 'page closed'}},
    )[0] == 'error'


def test_cancellation_between_assertions_never_sends_the_next_read():
    calls = []
    cancelled = [False]

    class OData:
        def get_count(self, entity):
            calls.append(entity)
            cancelled[0] = True
            return 1

    assertion = {'id': 'first', 'scope': 'business', 'check': 'odata_record_count', 'engine': 'odata',
                 'operator': 'count_equals', 'selector': {'entity': 'Document_Заказ'},
                 'expected': {'type': 'integer', 'value': 1}, 'source_ref': 'ТЗ'}
    _, _, details = evaluate_assertions(
        [assertion, {**assertion, 'id': 'second'}], OData(),
        should_cancel=lambda: cancelled[0],
    )
    assert calls == ['Document_Заказ']
    assert [item['assertion_id'] for item in details] == ['first']


def test_run_gate_rejects_case_less_legacy_auto_and_accepts_typed_case():
    from fastapi import HTTPException
    from testpilot.ai.api import _validate_run_assertions

    probes = {
        'odata': {'id': 'metadata', 'scope': 'metadata', 'check': 'metadata_entity',
                  'engine': 'odata', 'operator': 'exists', 'selector': {'entity': 'Catalog_Test'}},
        'playwright': {'id': 'web', 'scope': 'connectivity', 'check': 'web_section_visible',
                       'engine': 'playwright', 'operator': 'exists', 'selector': {'section': 'Продажи'},
                       'expected': {'type': 'boolean', 'value': True}},
    }
    for engine, assertion in probes.items():
        for proposal in ({'engine': engine}, {'engine': engine, 'cases': []}):
            payload = {'approval': {'selected_proposals': [0]},
                       'suggestion': {'proposals': [proposal]}}
            with pytest.raises(HTTPException) as error:
                _validate_run_assertions(payload)
            assert error.value.status_code == 422
        payload = {'approval': {'selected_proposals': [0]}, 'suggestion': {'proposals': [{
            'engine': engine, 'cases': [{'assertions': [assertion]}],
        }]}}
        assert _validate_run_assertions(payload) is None
    assert _validate_run_assertions({
        'approval': {'selected_proposals': [0]},
        'suggestion': {'proposals': [{'engine': 'manual', 'cases': []}]},
    }) is None
