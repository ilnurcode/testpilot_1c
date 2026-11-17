"""Чтение настроек окружения с проверкой на старте.

Числовые настройки не должны молча подменяться дефолтом: опечатка в окружении
ломает запуск с текстом, где видно имя переменной, фактическое значение и диапазон.
"""
from __future__ import annotations

import os


def int_env(name: str, default: int, minimum: int, maximum: int, *, raw: str | None = None) -> int:
    """Целое из окружения: пусто — default, иначе строгая проверка диапазона."""
    value = raw if raw is not None else os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    text = str(value).strip()
    try:
        parsed = int(text)
    except ValueError:
        raise ValueError(
            f'{name}={text!r}: нужно целое число от {minimum} до {maximum} '
            f'(пустое значение — по умолчанию {default})'
        ) from None
    if not minimum <= parsed <= maximum:
        raise ValueError(f'{name}={parsed}: нужно целое число от {minimum} до {maximum}')
    return parsed
