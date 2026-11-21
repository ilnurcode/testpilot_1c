"""Fail-closed проверка схемы: pending/чужая ревизия не выдаются за готовность.

Схемы создаются только реальными миграциями на одноразовом инстансе (A1-харнесс
через tests/support_pg.py); рабочая БД не затрагивается.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from support_pg import alembic_head, migrated_schema, session_factory
from testpilot import db
from testpilot.schema_state import SchemaStateError, describe, require_head

ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(ROOT / 'alembic.ini'))


def _downgrade_one(engine) -> None:
    config = _config()
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '-1')


def _schema_dsn(engine, schema: str) -> str:
    query = dict(engine.url.query)
    query['options'] = f'-csearch_path={schema}'
    return engine.url.set(query=query).render_as_string(hide_password=False)


@contextmanager
def client_on(engine):
    old_engine, old_factory = db._engine, db._factory
    db._engine, db._factory = engine, session_factory(engine)
    try:
        from testpilot.impact.api import app as impact
        with TestClient(impact) as client:
            yield client
    finally:
        db._engine, db._factory = old_engine, old_factory


def load_schema_actions():
    spec = importlib.util.spec_from_file_location('schema_actions', ROOT / 'scripts' / 'schema_actions.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_schema_state_is_ok_at_head():
    with migrated_schema() as (engine, _schema):
        with engine.connect() as connection:
            state = describe(connection)

    assert state['state'] == 'ok'
    assert state['current'] == alembic_head()
    assert state['heads'] == [alembic_head()]


def test_pending_schema_is_refused_by_require_head():
    with migrated_schema() as (engine, _schema):
        _downgrade_one(engine)
        with engine.connect() as connection:
            state = describe(connection)
            assert state['state'] == 'pending'
            with pytest.raises(SchemaStateError):
                require_head(connection)


def test_health_is_503_on_pending_schema():
    with migrated_schema() as (engine, _schema):
        _downgrade_one(engine)
        with client_on(engine) as client:
            response = client.get('/health')

    assert response.status_code == 503
    assert 'устарела' in response.json()['error']['message']


def test_health_is_503_on_unknown_future_revision():
    with migrated_schema() as (engine, _schema):
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '9999_future'"))
        with client_on(engine) as client:
            response = client.get('/health')

    assert response.status_code == 503
    assert 'новее кода' in response.json()['error']['message']


def test_health_is_503_on_uninitialized_schema():
    with migrated_schema() as (engine, _schema):
        with engine.begin() as connection:
            connection.execute(text('DROP TABLE alembic_version'))
        with engine.connect() as connection:
            assert describe(connection)['state'] == 'uninitialized'
        with client_on(engine) as client:
            response = client.get('/health')

    assert response.status_code == 503
    assert 'не инициализирована' in response.json()['error']['message']


def test_init_refuses_existing_schema_and_leaks_no_secret(capsys):
    module = load_schema_actions()
    with migrated_schema() as (engine, schema):
        exit_code = module.main(['--init', '--dsn', _schema_dsn(engine, schema)])
        password = engine.url.password

    captured = capsys.readouterr()
    assert exit_code == 2
    assert password and password not in captured.out + captured.err
    assert 'уже существует' in captured.err


def test_update_requires_confirmation_before_touching_schema():
    module = load_schema_actions()
    with migrated_schema() as (engine, schema):
        _downgrade_one(engine)
        exit_code = module.main(['--update', '--dsn', _schema_dsn(engine, schema)])
        with engine.connect() as connection:
            state = describe(connection)

    assert exit_code == 2
    assert state['state'] == 'pending', 'без --confirmed схема не должна меняться'


def test_confirmed_update_moves_pending_schema_to_head():
    module = load_schema_actions()
    with migrated_schema() as (engine, schema):
        _downgrade_one(engine)
        exit_code = module.main(['--update', '--confirmed', '--dsn', _schema_dsn(engine, schema)])
        with engine.connect() as connection:
            state = describe(connection)

    assert exit_code == 0
    assert state['state'] == 'ok' and state['current'] == alembic_head()


def test_confirmed_update_refuses_uninitialized_schema():
    module = load_schema_actions()
    with migrated_schema() as (engine, schema):
        with engine.begin() as connection:
            connection.execute(text('DROP TABLE alembic_version'))
        exit_code = module.main(['--update', '--confirmed', '--dsn', _schema_dsn(engine, schema)])

    assert exit_code == 2


def test_health_is_503_without_leaking_dsn_when_database_unreachable():
    """Недоступная БД — понятный 503, не success и не stacktrace с DSN."""
    from testpilot.schema_state import engine_for

    bad = engine_for('postgresql+psycopg://user:sekret-pass@127.0.0.1:1/none')
    try:
        with client_on(bad) as client:
            response = client.get('/health')
    finally:
        bad.dispose()

    assert response.status_code == 503
    message = response.json()['error']['message']
    assert 'недоступна' in message
    # В отказе нет DSN, учётных данных и трассировки.
    assert 'sekret-pass' not in message
    assert '://' not in message and '@' not in message
    assert 'Traceback' not in message
