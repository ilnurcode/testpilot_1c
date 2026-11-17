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
    'dependency_paths': [],
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
