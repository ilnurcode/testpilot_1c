"""Предел детализации замечаний аудита.

Значение читается из окружения один раз при старте процесса: абсурдная конфигурация
должна ломать запуск с понятным сообщением, а не молча подменяться дефолтом.
"""
from __future__ import annotations

import os

ENV_VAR = 'TP_AUDIT_MAX_ISSUES'
DEFAULT_MAX_ISSUES_KEPT = 5000
MIN_MAX_ISSUES_KEPT = 100
MAX_MAX_ISSUES_KEPT = 20000


def resolve_max_issues_kept(raw: str | None = None) -> int:
    """Разбирает и проверяет предел; пустое значение означает дефолт."""
    if raw is None:
        raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_MAX_ISSUES_KEPT
    text = raw.strip()
    try:
        value = int(text)
    except ValueError:
        raise ValueError(
            f'{ENV_VAR}={text!r}: нужно целое число от {MIN_MAX_ISSUES_KEPT} до {MAX_MAX_ISSUES_KEPT} '
            f'(пустое значение — предел по умолчанию {DEFAULT_MAX_ISSUES_KEPT})'
        ) from None
    if not MIN_MAX_ISSUES_KEPT <= value <= MAX_MAX_ISSUES_KEPT:
        raise ValueError(
            f'{ENV_VAR}={value}: допустимо от {MIN_MAX_ISSUES_KEPT} до {MAX_MAX_ISSUES_KEPT}'
        )
    return value


MAX_ISSUES_KEPT = resolve_max_issues_kept()
