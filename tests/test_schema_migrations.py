"""Схема тестов создаётся только миграциями; модель и миграции не расходятся."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from support_pg import alembic_head, migrated_schema
from testpilot.db import Base


def test_schema_is_created_at_migration_head():
    with migrated_schema() as (engine, schema):
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
            search_path = connection.execute(text('SHOW search_path')).scalar()
        assert revision == alembic_head(), 'миграции не довели схему до head'
        assert schema in (search_path or ''), 'search_path не изолирует одноразовую схему'


def test_model_and_migrations_have_no_drift():
    with migrated_schema() as (engine, _schema):
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            diff = compare_metadata(context, Base.metadata)
    assert diff == [], (
        'ORM-модель и миграции расходятся; обновите миграцию, а не create_all:\n'
        + '\n'.join(repr(item) for item in diff)
    )
