import pytest
from fastapi.testclient import TestClient

from testpilot import db
from support_pg import migrated_schema, session_factory


@pytest.fixture
def clients(monkeypatch):
    with migrated_schema() as (engine, _schema):
        old_engine, old_factory = db._engine, db._factory
        db._engine = engine
        db._factory = session_factory(engine)
        try:
            for key in ('TP_ANALYST_PASSWORD', 'TP_EXPERT_PASSWORD', 'TP_RUNNER_PASSWORD'):
                monkeypatch.setenv(key, 'test-password-only')
            from testpilot.impact.api import app as impact
            from testpilot.intelligence.api import app as intelligence

            with TestClient(impact) as impact_client, TestClient(intelligence) as intelligence_client:
                impact_client.auth = ('expert', 'test-password-only')
                intelligence_client.auth = ('expert', 'test-password-only')
                yield impact_client, intelligence_client
        finally:
            db._engine, db._factory = old_engine, old_factory
