"""Canonical JSON Schema files remain structurally and cross-reference valid."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / 'contracts'


def _schemas() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding='utf-8'))
        for path in sorted(CONTRACTS.glob('*-1.0.schema.json'))
    }


def test_contract_schemas_validate_and_resolve_assertion_reference():
    schemas = _schemas()
    assert schemas
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    assertion = schemas['ai-assertion-1.0.schema.json']
    suggestion = schemas['ai-suggestion-1.0.schema.json']
    registry = Registry().with_resources([
        ('https://testpilot.local/contracts/ai-assertion-1.0.schema.json', Resource.from_contents(assertion)),
        ('ai-assertion-1.0.schema.json', Resource.from_contents(assertion)),
    ])
    validator = Draft202012Validator(suggestion, registry=registry)
    valid = {
        'summary': 'Schema registry validation',
        'proposals': [{
            'title': 'Validate schema',
            'rationale': 'Cross-document reference resolution',
            'engine': 'manual',
            'object_ids': ['object-1'],
            'steps': ['Run validation'],
            'expected_result': 'Validation succeeds',
            'cases': [{'assertions': [{
                'id': 'assertion-1', 'scope': 'manual', 'check': 'manual',
                'engine': 'manual', 'operator': 'manual', 'requirement': 'Manual verification',
            }]}],
        }],
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, 'unexpected': True})
