"""Статус, проверка и явное управление ключом секретов подключения.

Секреты не печатаются. Windows по умолчанию использует DPAPI; portable AES-GCM
читает key material только из external key file. Ключ не создаётся при старте.

    python scripts/secrets_key.py --status
    python scripts/secrets_key.py --verify
    python scripts/secrets_key.py --migrate-legacy --confirmed
    python scripts/secrets_key.py --create-portable-key --key-file <absolute external path> --confirmed

Legacy-текст не удаляется до явной проверенной миграции. Зашифрованные записи
не заменяются автоматически: для другой схемы пароль вводится владельцем заново.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import text

from testpilot import secrets
from testpilot.contracts import now
from testpilot.db import engine

SECRET_COLUMNS = ('auth_secret', 'auth_secret_scheme', 'auth_secret_updated_at')


def columns_present(connection) -> bool:
    rows = connection.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'impact_projects' AND table_schema = current_schema() AND column_name IN "
        "('auth_secret', 'auth_secret_scheme', 'auth_secret_updated_at')"
    )).scalars().all()
    return len(set(rows)) == len(SECRET_COLUMNS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Секреты подключения: состояние, проверка и явное управление ключом.')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--status', action='store_true', help='доступность протектора и счётчики записей')
    action.add_argument('--verify', action='store_true', help='проверить расшифровку всех сохранённых секретов')
    action.add_argument('--migrate-legacy', action='store_true', help='зашифровать пароли, сохранённые открытым текстом')
    action.add_argument('--create-portable-key', action='store_true', help='явно создать новый external portable key file')
    parser.add_argument('--key-file', help='абсолютный путь вне checkout; применяется только с --create-portable-key')
    parser.add_argument('--confirmed', action='store_true', help='подтверждает проверенный backup перед миграцией или созданием ключа')
    args = parser.parse_args(argv)

    if args.create_portable_key:
        if not args.confirmed:
            print('Ключ не создан: нужен --confirmed.', file=sys.stderr)
            return 2
        if not args.key_file:
            print('Ключ не создан: нужен --key-file с абсолютным путём вне checkout.', file=sys.stderr)
            return 2
        try:
            secrets.create_portable_key(args.key_file)
        except secrets.SecretsError as exc:
            print(f'Ключ не создан: {exc}', file=sys.stderr)
            return 2
        print('Portable key material создан; сохраните его только во внешнем защищённом хранилище.')
        return 0
    if args.key_file:
        parser.error('--key-file применяется только с --create-portable-key')

    report: dict = {'protector': secrets.status()}
    with engine().connect() as connection:
        present = columns_present(connection)
        report['secret_columns'] = present
        if present:
            counts = connection.execute(text(
                'SELECT count(*) FILTER (WHERE auth_secret IS NOT NULL), '
                'count(*) FILTER (WHERE auth_password IS NOT NULL) FROM impact_projects'
            )).one()
            report['encrypted_rows'], report['legacy_plaintext_rows'] = int(counts[0]), int(counts[1])
        else:
            legacy = connection.execute(text(
                'SELECT count(*) FROM impact_projects WHERE auth_password IS NOT NULL'
            )).scalar()
            report['encrypted_rows'], report['legacy_plaintext_rows'] = 0, int(legacy or 0)

    if args.status:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if not report['protector']['available']:
            print('BLOCKED: сохранение секретов недоступно: '
                  f"{report['protector'].get('reason') or 'протектор недоступен'}", file=sys.stderr)
            return 3
        return 0

    if not report['protector']['available']:
        print('BLOCKED: операция не выполнена: '
              f"{report['protector'].get('reason') or 'протектор недоступен'}", file=sys.stderr)
        return 3
    if not report['secret_columns']:
        print('BLOCKED: нужна согласованная миграция столбцов секретов '
              '(auth_secret, auth_secret_scheme, auth_secret_updated_at).', file=sys.stderr)
        return 3

    if args.verify:
        failures = 0
        with engine().connect() as connection:
            rows = connection.execute(text(
                'SELECT id, auth_secret FROM impact_projects WHERE auth_secret IS NOT NULL'
            )).all()
        for row in rows:
            try:
                secrets.open_sealed(row[1], context=secrets.project_secret_context(row[0]))
            except secrets.SecretsError as exc:
                failures += 1
                print(f'FAIL {row[0]}: {exc}', file=sys.stderr)
        print(f'Проверено секретов: {len(rows)}, ошибок: {failures}')
        return 0 if failures == 0 else 2

    # --migrate-legacy
    if not args.confirmed:
        print('Миграция не выполнена: нужен --confirmed и проверенный backup. Открытый текст не удаляется.', file=sys.stderr)
        return 2
    migrated = 0
    with engine().begin() as connection:
        rows = connection.execute(text(
            'SELECT id, auth_password FROM impact_projects WHERE auth_password IS NOT NULL'
        )).all()
        for row in rows:
            plaintext = row[1]
            context = secrets.project_secret_context(row[0])
            envelope, scheme = secrets.seal(plaintext, context=context)
            if secrets.open_sealed(envelope, context=context) != plaintext:
                raise SystemExit(f'FAIL {row[0]}: roundtrip не совпал; запись не изменена')
            connection.execute(text(
                'UPDATE impact_projects SET auth_secret = :secret, auth_secret_scheme = :scheme, '
                'auth_secret_updated_at = :ts, auth_password = NULL WHERE id = :id'
            ), {'secret': envelope, 'scheme': scheme, 'ts': now(), 'id': row[0]})
            migrated += 1
    print(f'Зашифровано записей: {migrated}. Legacy-поле очищено только после проверенного roundtrip.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
