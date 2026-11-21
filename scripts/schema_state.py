"""Состояние схемы БД против головы миграций кода. Схему не меняет, секреты не печатает.

    python scripts/schema_state.py                 # печать состояния, код 0
    python scripts/schema_state.py --require-head   # код 3, если схема не на голове
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from testpilot.schema_state import STATE_OK, describe, engine_for, schema_error_message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Сверка ревизии БД с головой миграций.')
    parser.add_argument('--require-head', action='store_true',
                        help='ненулевой код (3), если схема не совпадает с головой миграций')
    parser.add_argument('--json', action='store_true', help='печать JSON вместо строки')
    parser.add_argument('--dsn', default=None, help='переопределить DATABASE_URL (значение не печатается)')
    args = parser.parse_args(argv)

    dsn = (args.dsn or os.environ.get('DATABASE_URL') or '').strip()
    if not dsn:
        print('DATABASE_URL не задан: загрузите .env или передайте --dsn.', file=sys.stderr)
        return 4

    engine = engine_for(dsn)
    try:
        with engine.connect() as connection:
            state = describe(connection)
    except Exception as exc:  # noqa: BLE001 - только тип ошибки, без DSN
        print(f'Не удалось прочитать состояние схемы: {type(exc).__name__}. Проверьте, что БД запущена.', file=sys.stderr)
        return 4
    finally:
        engine.dispose()

    if args.json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    else:
        current = state['current'] or 'нет'
        heads = ', '.join(state['heads']) or 'нет'
        print(f"Схема БД: {state['state']} (БД={current}, код={heads})")

    if state['state'] != STATE_OK:
        print(schema_error_message(state), file=sys.stderr)
        return 3 if args.require_head else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
