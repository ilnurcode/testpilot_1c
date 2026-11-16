"""Эвристика потенциально изменяющих шагов плана проверок: нужен явный opt-in перед прогоном."""
from __future__ import annotations

from typing import Any

# Эвристика по тексту кнопки: по плану не видно, что именно делает форма 1С,
# поэтому маркеры — подстроки в target у действия click (регистр не важен).
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


def step_is_mutating(action: str | None, target: str | None) -> bool:
    """Потенциально изменяет ли шаг данные базы: fill — всегда, click — по маркерам текста."""
    action = (action or '').strip().lower()
    if action == 'fill':
        return True
    if action == 'click':
        text = (target or '').casefold()
        return any(marker.casefold() in text for marker in MUTATING_CLICK_MARKERS)
    return False


def mutation_conflicts(suite: dict[str, Any]) -> list[str]:
    """Сообщения о потенциально изменяющих шагах среди утверждённых предложений плана."""
    selected = (suite.get('approval') or {}).get('selected_proposals') or []
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    conflicts: list[str] = []
    for index in selected:
        if not isinstance(index, int) or not 0 <= index < len(proposals):
            continue
        number = 0
        for case in proposals[index].get('cases') or []:
            for step in case.get('steps') or []:
                number += 1
                action = step.get('action')
                target = step.get('target') or ''
                if step_is_mutating(action, target):
                    conflicts.append(
                        f"Предложение {index + 1}: шаг {number} '{target or action}' потенциально изменяет данные; "
                        'передайте allow_mutations=true, если база тестовая'
                    )
    return conflicts
