"""Одноразовая схема PostgreSQL, созданная реальными миграциями Alembic.

Положительная идентичность: БД должна нести маркер владельца (`TP_TEST_INSTANCE_TOKEN`),
установленный только на ephemeral-инстансе. Имя БД и DSN сами по себе доказательством не являются.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent


def test_dsn() -> str:
    url = (os.environ.get('TP_TEST_DATABASE_URL') or '').strip()
    if not url:
        raise RuntimeError('TP_TEST_DATABASE_URL не задан: интеграционные тесты требуют отдельный одноразовый DSN')
    return url


def expected_database() -> str:
    name = (os.environ.get('TP_TEST_EXPECT_DB') or '').strip()
    if not name:
        raise RuntimeError('TP_TEST_EXPECT_DB не задан: без явной идентичности БД тесты не создают схемы')
    return name


def instance_token() -> str:
    token = (os.environ.get('TP_TEST_INSTANCE_TOKEN') or '').strip()
    if not token:
        raise RuntimeError(
            'TP_TEST_INSTANCE_TOKEN не задан: тестовая БД должна нести маркер владельца '
            '(устанавливается на ephemeral-инстансе, не на рабочей БД)'
        )
    return token


def _assert_database_identity(engine, expected: str, token: str) -> None:
    with engine.connect() as connection:
        actual, marker = connection.execute(text(
            "SELECT current_database(), shobj_description(oid, 'pg_database') "
            "FROM pg_database WHERE datname = current_database()"
        )).one()
    if actual != expected:
        raise RuntimeError(f'target identity mismatch: DSN указывает на БД {actual!r}, ожидалась {expected!r}')
    if marker != token:
        raise RuntimeError('target identity mismatch: маркер владельца БД не совпадает с TP_TEST_INSTANCE_TOKEN')


def alembic_head() -> str:
    config = Config(str(ROOT / 'alembic.ini'))
    return ScriptDirectory.from_config(config).get_current_head()


@contextmanager
def migrated_schema():
    """Создаёт уникальную схему, применяет все миграции и отдаёт движок с search_path.

    Никакого create_all/ручного alembic_version: схема и версия создаются только миграциями.
    """
    url = test_dsn()
    expected = expected_database()
    token = instance_token()

    admin = create_engine(url)
    _assert_database_identity(admin, expected, token)
    schema = 'tp_test_' + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        config = Config(str(ROOT / 'alembic.ini'))
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        head = alembic_head()
        if revision != head:
            raise RuntimeError(f'после миграций ревизия {revision!r}, ожидалась {head!r}')
        yield engine, schema
    finally:
        engine.dispose()
        # Повторная маркерная проверка непосредственно перед DROP: БД не должна смениться.
        _assert_database_identity(admin, expected, token)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
