"""Чтение настроек окружения с проверкой на старте.

Числовые настройки не должны молча подменяться дефолтом: опечатка в окружении
ломает запуск с текстом, где видно имя переменной, фактическое значение и диапазон.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

HTTP_SCHEMES = ('http', 'https')
DATABASE_SCHEMES = ('postgresql+psycopg', 'postgresql')


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


def choice_env(name: str, default: str, allowed: tuple[str, ...], *, raw: str | None = None) -> str:
    """Значение из фиксированного набора: неизвестное роняет старт, а не подменяется дефолтом."""
    value = raw if raw is not None else os.environ.get(name)
    text = (value or '').strip().lower() or default
    if text not in allowed:
        raise ValueError(f'{name}: ожидается ' + ' или '.join(allowed))
    return text


def url_env(
    name: str,
    default: str | None = None,
    schemes: tuple[str, ...] = HTTP_SCHEMES,
    required: bool = False,
    *,
    example: str = 'https://api.example.com/v1',
    raw: str | None = None,
) -> str | None:
    """URL из окружения: пусто — default (или падение при required), иначе строгая проверка.

    Опечатка в схеме не должна выясняться в рантайме: проверяем на старте.
    Пробелы по краям и внутри значения считаются ошибкой, а не нормализуются молча.
    """
    value = raw if raw is not None else os.environ.get(name)
    if value is None or not str(value).strip():
        if required:
            raise ValueError(f"{name}: переменная обязательна — нужен URL со схемой {'/'.join(schemes)} и хостом (например {example})")
        return default
    text = str(value)
    allowed = ' или '.join(schemes)
    hint = f'нужен URL со схемой {allowed} и хостом (например {example})'
    if text != text.strip() or ' ' in text:
        raise ValueError(f'{name}={text!r}: {hint}')
    parsed = urlparse(text)
    if parsed.scheme not in schemes or not parsed.hostname:
        raise ValueError(f'{name}={text!r}: {hint}')
    return text


def validate_startup_urls() -> None:
    """Проверка URL-настроек при старте сервиса: значения по умолчанию — как в коде/compose."""
    url_env('TP_LLM_BASE_URL', default='https://api.openai.com/v1')
    url_env('TP_1C_ODATA_URL')
    url_env('TP_1C_WEB_URL')
    url_env('IMPACT_URL', default='http://127.0.0.1:8001')
    url_env('INTELLIGENCE_URL', default='http://127.0.0.1:8002')
    url_env('WEB_URL', default='http://127.0.0.1:8000')
    url_env('DATABASE_URL', schemes=DATABASE_SCHEMES,
            example='postgresql+psycopg://user:pass@db:5432/name')
