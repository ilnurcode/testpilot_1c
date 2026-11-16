"""Пометки и режимы прогона плана проверок: эвристика по тексту кнопки и строгий read-only режим."""
from __future__ import annotations

from typing import Any

# Эвристика по тексту кнопки: по плану не видно, что именно делает форма 1С,
# поэтому маркеры — подстроки в target у действия click (регистр не важен).
# Распознавание неполное: кнопки «ОК», «Подтвердить», пользовательские подписи сюда не входят.
MUTATING_CLICK_MARKERS = (
    'Записать',
    'Провести',
    'Создать',
    'Удалить',
    'Отменить проведение',
    'Пометить на удаление',
    'Скопировать',
    'Изменить',
)

# Строгий режим не полагается на текст кнопки: разрешены только действия без ввода и кликов.
READ_ONLY_ACTIONS = frozenset({'assert_text', 'assert_section', 'wait', 'open_url'})

_HEURISTIC_NOTE = (
    'распознавание неполное: кнопки с другими подписями (ОК, Подтвердить, пользовательские) не блокируются'
)


def step_is_mutating(action: str | None, target: str | None) -> bool:
    """Потенциально изменяет ли шаг данные базы по эвристике: fill — всегда, click — по маркерам текста."""
    action = (action or '').strip().lower()
    if action == 'fill':
        return True
    if action == 'click':
        text = (target or '').casefold()
        return any(marker.casefold() in text for marker in MUTATING_CLICK_MARKERS)
    return False


def proposal_is_mutating(proposal: dict[str, Any]) -> bool:
    """Есть ли в предложении хотя бы один потенциально изменяющий шаг (по всем cases)."""
    for case in proposal.get('cases') or []:
        for step in case.get('steps') or []:
            if step_is_mutating(step.get('action'), step.get('target')):
                return True
    return False


def annotate_proposals(payload: dict[str, Any]) -> dict[str, Any]:
    """Копия payload с флагом mutating у каждого предложения — тот же расчёт, что у блокировки прогона."""
    suggestion = dict((payload or {}).get('suggestion') or {})
    if not suggestion:
        return dict(payload or {})
    proposal_list = suggestion.get('proposals') or []
    suggestion['proposals'] = [
        {**proposal, 'mutating': proposal_is_mutating(proposal)} for proposal in proposal_list
    ]
    return {**payload, 'suggestion': suggestion}


def _approved(suite: dict[str, Any]) -> list[int]:
    selected = (suite.get('approval') or {}).get('selected_proposals') or []
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    return [index for index in selected if isinstance(index, int) and 0 <= index < len(proposals)]


def mutation_conflicts(suite: dict[str, Any]) -> list[str]:
    """Потенциально изменяющие шаги утверждённых предложений (эвристика по подписи кнопки)."""
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    conflicts: list[str] = []
    for index in _approved(suite):
        number = 0
        for case in proposals[index].get('cases') or []:
            for step in case.get('steps') or []:
                number += 1
                action = step.get('action')
                target = step.get('target') or ''
                if step_is_mutating(action, target):
                    conflicts.append(
                        f"Предложение {index + 1}: шаг {number} '{target or action}' потенциально изменяет данные "
                        f'(эвристическая защита по тексту кнопки); передайте allow_mutations=true, если база тестовая. '
                        f'{_HEURISTIC_NOTE[0].upper()}{_HEURISTIC_NOTE[1:]}.'
                    )
    return conflicts


def strict_conflicts(suite: dict[str, Any]) -> list[str]:
    """Шаги вне белого списка read-only действий утверждённых предложений (строгий режим)."""
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    conflicts: list[str] = []
    for index in _approved(suite):
        number = 0
        for case in proposals[index].get('cases') or []:
            for step in case.get('steps') or []:
                number += 1
                action = (step.get('action') or '').strip().lower()
                if action in READ_ONLY_ACTIONS:
                    continue
                conflicts.append(
                    f"Предложение {index + 1}: шаг {number} действие '{action}' недопустимо в строгом режиме "
                    f'(разрешены только {", ".join(sorted(READ_ONLY_ACTIONS))}); '
                    'уберите strict_read_only или запускайте на тестовой базе с allow_mutations=true'
                )
    return conflicts


def mutating_proposal_count(payload: dict[str, Any]) -> int:
    """Сколько предложений плана содержат потенциально изменяющие шаги (для сводки истории)."""
    proposals = ((payload or {}).get('suggestion') or {}).get('proposals') or []
    return sum(1 for proposal in proposals if proposal_is_mutating(proposal))
