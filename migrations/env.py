from alembic import context
from testpilot.db import Base, engine

connection = context.config.attributes.get('connection')
if connection is not None:
    # Тестовая изоляция: миграции идут в переданное соединение с search_path
    # одноразовой схемы, а не в БД по умолчанию.
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
