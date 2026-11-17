"""Предел детализации замечаний аудита.

Значение читается из окружения один раз при старте процесса: абсурдная конфигурация
должна ломать запуск с понятным сообщением, а не молча подменяться дефолтом.
"""
from __future__ import annotations

from testpilot.config import int_env

ENV_VAR = 'TP_AUDIT_MAX_ISSUES'
DEFAULT_MAX_ISSUES_KEPT = 5000
MIN_MAX_ISSUES_KEPT = 100
MAX_MAX_ISSUES_KEPT = 20000


def resolve_max_issues_kept(raw: str | None = None) -> int:
    """Разбирает и проверяет предел; пустое значение означает дефолт."""
    return int_env(ENV_VAR, DEFAULT_MAX_ISSUES_KEPT, MIN_MAX_ISSUES_KEPT, MAX_MAX_ISSUES_KEPT, raw=raw)


MAX_ISSUES_KEPT = resolve_max_issues_kept()
