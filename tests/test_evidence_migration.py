"""Миграция 0009: создаётся created_at, старые строки остаются NULL, новые получают время.

Рабочая БД не меняется: тест создаёт собственную базу в том же PostgreSQL и удаляет её.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text


def _url_parts():
    url = os.environ.get('DATABASE_URL')
    if not url:
        pytest.fail('Тест миграции требует DATABASE_URL PostgreSQL')
    base, _ = url.rsplit('/', 1)
    return base, url


@pytest.fixture
def temp_database():
    base, _ = _url_parts()
    name = 'tp_mig_' + uuid.uuid4().hex[:8]
    admin = create_engine(f'{base}/postgres', isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS {name}'))
        connection.execute(text(f'CREATE DATABASE {name}'))
    url = f'{base}/{name}'
    try:
        yield url, name
    finally:
        engine = create_engine(url)
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
        admin.dispose()


def _alembic(url: str, *args: str) -> str:
    env = {**os.environ, 'DATABASE_URL': url}
    result = subprocess.run(['alembic', *args], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f'alembic {" ".join(args)}: {result.stderr[-800:]}'
    return result.stdout


def test_migration_keeps_old_rows_null_and_stamps_new_ones(temp_database):
    url, _name = temp_database
    _alembic(url, 'upgrade', '0008')

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO ti_packages(id,checksum,configuration_id,payload) "
            "VALUES ('pkg-mig','" + '0' * 64 + "','cfg','{}')"
        ))
        connection.execute(text(
            "INSERT INTO ti_ai_suites(id,package_id,project_id,status,payload,created_at) "
            "VALUES ('suite-mig','pkg-mig','p','approved','{}','2026-01-01T00:00:00Z')"
        ))
        connection.execute(text(
            "INSERT INTO ti_ai_evidence(id,suite_id,content) VALUES ('ev-old','suite-mig','\\x89504e47')"
        ))

    current = _alembic(url, 'current')
    assert '0009' not in current, f'до upgrade на 0008 не должно быть 0009: {current}'
    _alembic(url, 'upgrade', 'head')
    current = _alembic(url, 'current')
    assert '0009 (head)' in current, current

    with engine.begin() as connection:
        nullable = connection.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='ti_ai_evidence' AND column_name='created_at'"
        )).scalar()
        old_created = connection.execute(text(
            "SELECT created_at FROM ti_ai_evidence WHERE id='ev-old'"
        )).scalar()
        connection.execute(text(
            "INSERT INTO ti_ai_evidence(id,suite_id,content) VALUES ('ev-new','suite-mig','\\x89504e47')"
        ))
        new_created = connection.execute(text(
            "SELECT created_at FROM ti_ai_evidence WHERE id='ev-new'"
        )).scalar()
    engine.dispose()

    assert nullable == 'YES', 'created_at должен допускать NULL для старых строк'
    assert old_created is None, 'старая строка не должна получать время миграции'
    assert new_created is not None, 'новая строка должна получать время через DEFAULT'
    if new_created.tzinfo is None:
        new_created = new_created.replace(tzinfo=timezone.utc)
    assert abs(datetime.now(timezone.utc) - new_created).total_seconds() < 120
