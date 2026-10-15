import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from testpilot import db

@pytest.fixture
def clients(monkeypatch):
    url=os.environ.get('DATABASE_URL')
    if not url:pytest.fail('Интеграционные тесты требуют DATABASE_URL PostgreSQL; см. README')
    schema='tp_test_'+uuid4().hex
    admin=create_engine(url)
    with admin.begin() as c:c.execute(text(f'CREATE SCHEMA {schema}'))
    e=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    old_e,old_f=db._engine,db._factory
    db._engine=e;db._factory=sessionmaker(e,expire_on_commit=False)
    db.Base.metadata.create_all(e)
    with e.begin() as c:
        c.execute(text('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)'))
        c.execute(text("INSERT INTO alembic_version VALUES ('0004')"))
    for key in ['TP_ANALYST_PASSWORD','TP_EXPERT_PASSWORD','TP_RUNNER_PASSWORD']:monkeypatch.setenv(key,'test-password-only')
    from testpilot.impact.api import app as impact
    from testpilot.intelligence.api import app as intelligence
    with TestClient(impact) as a,TestClient(intelligence) as b:
        a.auth=('expert','test-password-only');b.auth=('expert','test-password-only')
        yield a,b
    e.dispose();db._engine=old_e;db._factory=old_f
    with admin.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()
