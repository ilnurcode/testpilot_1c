"""Сверка ревизии схемы БД с головой миграций кода.

Обычный запуск приложения схему не меняет: единственный источник схемы — версии
миграций. Здесь только чтение состояния; обновление — отдельное явное действие
(`scripts/update.ps1`, `docker compose --profile init run --rm migrate`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parent.parent

# Проверки состояния не должны висеть: недоступная БД — быстрый отказ, не «ожидание».
CONNECT_TIMEOUT_SECONDS = 5

STATE_OK = 'ok'
STATE_PENDING = 'pending'
STATE_AHEAD = 'ahead'
STATE_UNINITIALIZED = 'uninitialized'
STATE_UNKNOWN = 'unknown'


class SchemaStateError(RuntimeError):
    """Схема БД не совпадает с кодом: работа запрещена до явного обновления."""


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / 'alembic.ini')))


@lru_cache(maxsize=1)
def migration_heads() -> tuple[str, ...]:
    """Головы миграций, поставляемые с этим кодом.

    Код на диске не меняется во время работы процесса, поэтому результат кешируется.
    """
    return tuple(sorted(_script_directory().get_heads()))


@lru_cache(maxsize=1)
def known_revisions() -> frozenset[str]:
    return frozenset(
        script.revision
        for script in _script_directory().walk_revisions()
        if script.revision
    )


def current_revision(connection) -> str | None:
    """Ревизия в БД или None, если таблицы версии нет."""
    try:
        return connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
    except SQLAlchemyError:
        return None


def engine_for(dsn: str):
    """Движок с ограниченным временем подключения: статус-команды не должны висеть."""
    return create_engine(dsn, connect_args={'connect_timeout': CONNECT_TIMEOUT_SECONDS})


def describe(connection) -> dict:
    """Состояние схемы: ok / uninitialized / pending / ahead / unknown."""
    heads = migration_heads()
    current = current_revision(connection)
    if not heads:
        state = STATE_UNKNOWN
    elif current is None:
        state = STATE_UNINITIALIZED
    elif current in heads:
        state = STATE_OK
    elif current in known_revisions():
        state = STATE_PENDING
    else:
        state = STATE_AHEAD
    return {'state': state, 'current': current, 'heads': list(heads)}


def schema_error_message(state: dict) -> str:
    """Понятный отказ без DSN и секретов."""
    current = state.get('current') or 'нет'
    heads = ', '.join(state.get('heads') or ()) or 'нет'
    kind = state.get('state')
    if kind == STATE_UNINITIALIZED:
        return (
            f'Схема БД не инициализирована (таблица alembic_version отсутствует), '
            f'ожидаемая ревизия кода: {heads}. Обычный запуск схему не создаёт. '
            'Выполните явную инициализацию: scripts\\init.ps1 (Windows) или '
            'docker compose --profile init run --rm migrate.'
        )
    if kind == STATE_PENDING:
        return (
            f'Схема БД устарела: БД={current}, код={heads}. Обычный запуск схему не меняет. '
            'После проверенного backup выполните явное обновление: scripts\\update.ps1 -Confirmed '
            '(Windows) или docker compose --profile init run --rm migrate.'
        )
    if kind == STATE_AHEAD:
        return (
            f'БД новее кода: БД={current}, код={heads}. Понижение схемы автоматически не выполняется. '
            'Установите версию приложения, совместимую с этой БД.'
        )
    return f'Не удалось определить состояние схемы: БД={current}, код={heads}. Проверьте подключение к БД.'


def require_head(connection) -> dict:
    """Возвращает состояние схемы или бросает SchemaStateError (fail-closed)."""
    state = describe(connection)
    if state['state'] != STATE_OK:
        raise SchemaStateError(schema_error_message(state))
    return state
