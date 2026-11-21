"""Исполнимость локального плана как контракт: схемы, метаданные и демо-исполнитель.

Проверяются разрывы, найденные на стенде:
* EntitySet OData брался из префикса идентификатора с выдуманным запасным
  `Catalog_Номенклатура` вместо метаданных объекта (проверялся посторонний объект);
* full_name с пробелом или длинное имя роняли генерацию ValidationError вместо
  честного отказа от OData-проверки;
* демо-исполнитель принимал любой план синтетического пакета и завершал кейсы
  статусом error: в кейсах нет quantity/price/discount/expected_total.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from testpilot.ai import provider
from testpilot.ai.api import DEMO_CASE_INPUTS, _demo_plan_shape_error, _is_demo_plan

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / 'contracts'

PRESENTATION_AFFECTED = ['audit', 'discount', 'event', 'form', 'legacy', 'order', 'post', 'prices', 'sales_module', 'sum']
PRESENTATION_NAMES = {
    'audit': 'ОбщийМодуль.Аудит',
    'client': 'Справочник.Контрагенты',
    'discount': 'Продажи.Скидка',
    'form': 'Документ.ЗаказКлиента.Форма',
    'item': 'Справочник.Номенклатура',
    'order': 'Документ.ЗаказКлиента',
    'prices': 'РегистрСведений.Цены',
    'sales': 'РегистрНакопления.Продажи',
    'sales_module': 'ОбщийМодуль.Продажи',
    'sum': 'Продажи.РассчитатьСумму',
}


def package(affected=None):
    """Пакет в форме ответа impact-анализа пути OData (object_id — короткие идентификаторы)."""
    affected = list(affected or PRESENTATION_AFFECTED)
    return {
        'affected_objects': affected,
        'changed_objects': affected[:1],
        'configuration_id': 'presentation-sales',
        'target_version': '2.0',
        'dependency_paths': [],
        'warnings': [],
        'synthetic': True,
    }


def plan(affected=None, names=None):
    return provider.generate_local_1c_tests(package(affected), [], PRESENTATION_NAMES if names is None else names)


def _registry():
    assertion = json.loads((CONTRACTS / 'ai-assertion-1.0.schema.json').read_text(encoding='utf-8'))
    return Registry().with_resources([
        ('https://testpilot.local/contracts/ai-assertion-1.0.schema.json', Resource.from_contents(assertion)),
        ('ai-assertion-1.0.schema.json', Resource.from_contents(assertion)),
    ])


def test_local_plan_matches_contract_schema():
    suggestion = json.loads((CONTRACTS / 'ai-suggestion-1.0.schema.json').read_text(encoding='utf-8'))
    payload = plan().model_dump(mode='json')

    Draft202012Validator(suggestion, registry=_registry()).validate(payload)
    actions = {step['action'] for proposal in payload['proposals'] for case in proposal['cases']
               for step in case.get('steps') or []}
    assert actions <= {'click', 'wait', 'assert_text', 'assert_section', 'fill'}
    assert actions, 'playwright-кейс без шагов не исполним'


def test_odata_entity_comes_from_metadata_of_affected_object():
    """Документ области влияния важнее справочника-соседа: проверяется затронутый EntitySet."""
    odata = [item for item in plan().proposals if item.engine == 'odata']
    assert len(odata) == 1
    case = odata[0].cases[0]
    assert case.entity == 'Document_ЗаказКлиента'
    assert case.assertions[0].selector.entity == case.entity
    assert odata[0].object_ids == ['order']
    assert set(odata[0].object_ids) <= set(PRESENTATION_AFFECTED)


def test_no_odata_proposal_without_publishable_object():
    """Функции/модули не публикуются как EntitySet: посторонняя сущность не проверяется."""
    suggestion = plan(['sum', 'post'], names={'sum': 'Продажи.РассчитатьСумму'})

    assert not [item for item in suggestion.proposals if item.engine == 'odata']
    assert any('OData-проверка не построена' in warning for warning in suggestion.warnings)
    scopes = {assertion.scope for item in suggestion.proposals for case in item.cases
              for assertion in case.assertions}
    assert 'metadata' not in scopes


def test_untyped_full_name_does_not_break_generation():
    """Пробел в full_name (.epf/.erf) раньше давал ValidationError и HTTP 500 вместо плана."""
    for object_id, full_name in (('document.заказклиента', 'Документ.Заказ клиента'),
                                 ('processing.учебнаяобработка', 'ВнешняяОбработка.Учебная обработка')):
        suggestion = plan([object_id], names={object_id: full_name})

        assert not [item for item in suggestion.proposals if item.engine == 'odata'], full_name
        assert suggestion.warnings


def test_long_full_name_stays_within_text_limits():
    long_name = 'Документ.' + 'ОченьДлинноеИмяОбъекта' * 12
    suggestion = plan(['document.заказклиента'], names={'document.заказклиента': long_name})

    for proposal in suggestion.proposals:
        assert len(proposal.title) <= 300 and len(proposal.rationale) <= 300
        for case in proposal.cases:
            for assertion in case.assertions:
                assert len(assertion.requirement or '') <= 300


def _demo_payload(suggestion):
    return {
        'synthetic': True,
        'configuration_id': 'presentation-sales',
        'suggestion': suggestion.model_dump(mode='json'),
        'approval': {'selected_proposals': list(range(len(suggestion.proposals)))},
    }


def test_demo_executor_accepts_only_demo_case_data():
    demo = _demo_payload(provider.demo_suggestion(package()))
    assert _is_demo_plan(demo) and _demo_plan_shape_error(demo) is None
    for proposal in demo['suggestion']['proposals']:
        for case in proposal['cases']:
            assert set(DEMO_CASE_INPUTS) <= set(case)


def test_typed_local_plan_is_refused_by_demo_executor_with_clear_reason():
    """Раньше такой план проходил approve, а run-demo отдавал error «браузерный шаг»."""
    local = _demo_payload(plan())
    assert _is_demo_plan(local), 'identity синтетического пакета сохраняется'
    error = _demo_plan_shape_error(local)

    assert error and 'quantity/price/discount/expected_total' in error
    assert 'provider=demo' in error
