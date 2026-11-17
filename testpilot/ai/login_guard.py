"""Предохранитель от блокировки учётной записи 1С.

Если за последние `TP_1C_LOGIN_COOLDOWN_MINUTES` минут было `TP_1C_LOGIN_MAX_FAILURES` неудачных
попыток входа (или больше), новый прогон с playwright-сценарием отклоняется 429: браузер не запускается.
Успешный вход сбрасывает счётчик — считаем только серию неудач до последнего успеха.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from testpilot.config import int_env
from testpilot.db import AISuiteRow

COOLDOWN_MINUTES = int_env('TP_1C_LOGIN_COOLDOWN_MINUTES', 30, 1, 1440)
MAX_FAILURES = int_env('TP_1C_LOGIN_MAX_FAILURES', 3, 1, 10)
LOGIN_FAILURE_STAGES = ('after_click', 'submit')
LOGIN_FAILED_TEXT = 'Вход в 1С не выполнен'


def has_login_playwright(suite: dict) -> bool:
    """В плане есть playwright-предложение: исполнитель всегда выполняет вход перед шагами."""
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    return any((proposal.get('engine') or '') == 'playwright' for proposal in proposals)


def _result_login_failed(result: dict) -> bool:
    login = result.get('login') or {}
    if login.get('stage') in LOGIN_FAILURE_STAGES and login.get('form_visible_after') is True:
        return True
    return (result.get('status') or '') == 'error' and LOGIN_FAILED_TEXT in str(result.get('message') or '')


def _result_login_succeeded(result: dict) -> bool:
    login = result.get('login') or {}
    return (result.get('status') or '') == 'passed' and login.get('form_visible_after') is False


def recent_login_failures(s, project_id: str, *, now: datetime | None = None) -> int:
    """Число неудачных попыток входа в окне; новая успешная попытка обнуляет серию."""
    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(minutes=COOLDOWN_MINUTES)).strftime('%Y-%m-%dT%H:%M:%S')
    rows = s.execute(
        select(AISuiteRow.payload, AISuiteRow.created_at).where(AISuiteRow.project_id == project_id)
    ).all()
    entries = []
    for payload, created_at in rows:
        payload = payload or {}
        stamp = str(payload.get('created_at') or created_at or '')
        if stamp < cutoff:
            continue
        results = payload.get('results') or []
        if results:
            entries.append((stamp, results))
    entries.sort(key=lambda item: item[0], reverse=True)
    failures = 0
    for _stamp, results in entries:
        playwright = [result for result in results if (result.get('engine') or '') == 'playwright']
        if any(_result_login_succeeded(result) for result in playwright):
            break
        if any(_result_login_failed(result) for result in playwright):
            failures += 1
    return failures


def ensure_login_allowed(s, suite: dict, project_id: str) -> None:
    """429 до запуска браузера, если серия неудачных входов исчерпала лимит."""
    from fastapi import HTTPException

    if not has_login_playwright(suite):
        return
    failures = recent_login_failures(s, project_id)
    if failures >= MAX_FAILURES:
        raise HTTPException(
            429,
            f'Вход в 1С приостановлен: {failures} неудачных попыток за {COOLDOWN_MINUTES} минут. '
            'Это защита от блокировки учётной записи — проверьте учётные данные и повторите позже.',
        )
