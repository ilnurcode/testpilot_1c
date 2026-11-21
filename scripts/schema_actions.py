"""Явная инициализация/обновление схемы БД. Обычный запуск эти действия не выполняет.

Использование (внутри venv, DATABASE_URL из .env уже загружен):
    python scripts/schema_actions.py --init
    python scripts/schema_actions.py --update --confirmed

Правила:
- `--init` допустим только для неинициализированной схемы;
- `--update` требует `--confirmed` (проверенный backup) и никогда не понижает схему;
- после действия ревизия БД сверяется с головой миграций; несовпадение — ошибка.
"""
from __future__ import annotations

import argparse
import sys

from alembic import command
from alembic.config import Config

from testpilot.schema_state import (
    ROOT,
    STATE_AHEAD,
    STATE_OK,
    STATE_PENDING,
    STATE_UNINITIALIZED,
    describe,
    engine_for,
    schema_error_message,
)


def _print_state(prefix: str, state: dict) -> None:
    current = state.get('current') or 'нет'
    heads = ', '.join(state.get('heads') or ()) or 'нет'
    print(f'{prefix}: БД={current}, код={heads}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Явная инициализация или обновление схемы TestPilot.')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--init', action='store_true', help='создать схему с нуля (только для неинициализированной БД)')
    action.add_argument('--update', action='store_true', help='обновить схему до головы миграций (нужен --confirmed)')
    parser.add_argument('--confirmed', action='store_true', help='подтверждает проверенный backup перед обновлением')
    parser.add_argument('--dsn', default=None, help='переопределить DATABASE_URL (значение не печатается)')
    args = parser.parse_args(argv)

    import os

    dsn = (args.dsn or os.environ.get('DATABASE_URL') or '').strip()
    if not dsn:
        print('DATABASE_URL не задан: загрузите .env или передайте --dsn.', file=sys.stderr)
        return 4

    engine = engine_for(dsn)
    try:
        with engine.connect() as connection:
            state = describe(connection)
        _print_state('Схема до действия', state)

        if args.init:
            if state['state'] != STATE_UNINITIALIZED:
                print(
                    'Инициализация не выполнена: схема уже существует. '
                    'Для существующей БД используйте --update (после проверенного backup).',
                    file=sys.stderr,
                )
                return 2
        else:
            if not args.confirmed:
                print(
                    'Обновление не выполнено: нужен флаг --confirmed и проверенный backup/restore. '
                    'Обычный запуск схему не меняет.',
                    file=sys.stderr,
                )
                return 2
            if state['state'] == STATE_UNINITIALIZED:
                print('Обновление не выполнено: схема не инициализирована. Используйте --init.', file=sys.stderr)
                return 2
            if state['state'] == STATE_AHEAD:
                print(schema_error_message(state), file=sys.stderr)
                return 2
            if state['state'] == STATE_OK:
                print('Схема уже на голове миграций; изменений не требуется.')
                return 0
            if state['state'] != STATE_PENDING:
                print(schema_error_message(state), file=sys.stderr)
                return 2

        config = Config(str(ROOT / 'alembic.ini'))
        try:
            with engine.begin() as connection:
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
        except Exception as exc:  # noqa: BLE001 - отчёт без DSN
            print(f'Миграция завершилась ошибкой: {type(exc).__name__}. Схема могла остаться частично изменённой; '
                  'не запускайте приложение, проверьте состояние и восстановите проверенный backup.', file=sys.stderr)
            return 3

        with engine.connect() as connection:
            after = describe(connection)
        _print_state('Схема после действия', after)
        if after['state'] != STATE_OK:
            print(schema_error_message(after), file=sys.stderr)
            return 3
        print('Готово: схема на голове миграций. Дальше — обычный запуск (scripts\\start.ps1).')
        return 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
