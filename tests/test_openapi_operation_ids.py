"""Страж OpenAPI: operationId уникален в пределах сервиса (иначе ломаются генераторы клиентов)."""
from __future__ import annotations

import warnings
from collections import defaultdict

import pytest

MIN_OPERATIONS = 60


def _apps():
    from testpilot.impact.api import app as impact_app
    from testpilot.intelligence.api import app as intelligence_app
    from testpilot.web.api import app as web_app

    return {'web': web_app, 'impact': impact_app, 'intelligence': intelligence_app}


def _operation_ids(schema: dict) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = defaultdict(list)
    for path, operations in schema['paths'].items():
        for method, spec in operations.items():
            ids[spec['operationId']].append(f'{method.upper()} {path}')
    return dict(ids)


@pytest.mark.parametrize('service', ['web', 'impact', 'intelligence'])
def test_operation_ids_are_unique_per_service(service):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        schema = _apps()[service].openapi()

    operations = sum(len(spec) for spec in schema['paths'].values())
    assert operations >= MIN_OPERATIONS // 3, f'{service}: подозрительно мало операций ({operations})'

    duplicates = {oid: places for oid, places in _operation_ids(schema).items() if len(places) > 1}
    assert not duplicates, f'{service}: дубли operationId:\n' + '\n'.join(
        f'  {oid}: {", ".join(places)}' for oid, places in duplicates.items()
    )

    duplicate_warnings = [str(item.message) for item in caught if 'Duplicate Operation ID' in str(item.message)]
    assert not duplicate_warnings, f'{service}: FastAPI предупреждает о дублях: {duplicate_warnings}'


def test_all_services_together_have_enough_operations():
    total = sum(sum(len(spec) for spec in app.openapi()['paths'].values()) for app in _apps().values())

    assert total >= MIN_OPERATIONS, f'собрано {total} операций — похоже, схемы не собираются'
