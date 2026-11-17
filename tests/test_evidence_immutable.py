"""Инвариант неизменяемости evidence: только INSERT и каскадный DELETE, без UPDATE/upsert.

Первый тест — статический страж по исходникам testpilot/: любая попытка перезаписи ti_ai_evidence
должна валить тест. Второй — фактическая проверка на БД: повторная вставка того же id падает на PK.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / 'testpilot'

WRITE_PATTERNS = (
    'update(',
    'merge(',
    'bulk_update',
    'bulk_insert',
    'on_conflict',
    'ON CONFLICT',
    'INSERT INTO ti_ai_evidence',
    'values(',
)

# Строки, где EvidenceRow упоминается легально: импорт, модель, вставка, чтение, удаление каскадом.
ALLOWED_MARKERS = (
    'import',
    'class EvidenceRow',
    '__tablename__',
    's.add(EvidenceRow(',
    'delete(EvidenceRow)',
    's.get(EvidenceRow',
    'select(EvidenceRow',
    'EvidenceRow.id',
    'EvidenceRow.suite_id',
    'EvidenceRow.content',
)


def _evidence_write_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(ROOT.rglob('*.py')):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if 'EvidenceRow' not in line and 'ti_ai_evidence' not in line:
                continue
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if any(marker in line for marker in ALLOWED_MARKERS):
                continue
            if any(pattern in line for pattern in WRITE_PATTERNS):
                violations.append(f'{path.relative_to(ROOT.parent)}:{number}: {stripped}')
    return violations


def test_evidence_table_has_no_update_or_upsert_operations():
    violations = _evidence_write_violations()

    assert violations == [], (
        'evidence должен быть неизменяемым: найдены операции перезаписи ti_ai_evidence (допустимы только '
        'session.add(EvidenceRow(...)) и каскадные delete):\n' + '\n'.join(violations)
    )


def test_second_insert_with_same_evidence_id_fails(clients):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from testpilot import db

    factory = sessionmaker(db.engine(), expire_on_commit=False)
    evidence_id = '00000000-0000-0000-0000-000000000001'
    suite_id = '00000000-0000-0000-0000-0000000000aa'
    with factory() as s:
        s.add(db.PackageRow(id='pkg-immutable', checksum='0' * 64, configuration_id='cfg', payload={}))
        s.flush()
        s.add(db.AISuiteRow(id=suite_id, package_id='pkg-immutable', project_id='immutable-test',
                            status='approved', payload={}))
        s.flush()
        s.add(db.EvidenceRow(id=evidence_id, suite_id=suite_id, content=b'first'))
        s.commit()
    try:
        with factory() as s:
            s.add(db.EvidenceRow(id=evidence_id, suite_id=suite_id, content=b'overwrite'))
            with pytest.raises(IntegrityError):
                s.commit()
    finally:
        with factory() as s:
            row = s.get(db.EvidenceRow, evidence_id)
            if row is not None:
                s.delete(row)
            suite = s.get(db.AISuiteRow, suite_id)
            if suite is not None:
                s.delete(suite)
            package = s.get(db.PackageRow, 'pkg-immutable')
            if package is not None:
                s.delete(package)
            s.commit()


def _seed_suite(factory, db, suite_id='00000000-0000-0000-0000-0000000000bb'):
    with factory() as s:
        if s.get(db.PackageRow, 'pkg-immutable2') is None:
            s.add(db.PackageRow(id='pkg-immutable2', checksum='1' * 64, configuration_id='cfg', payload={}))
            s.flush()
        if s.get(db.AISuiteRow, suite_id) is None:
            s.add(db.AISuiteRow(id=suite_id, package_id='pkg-immutable2', project_id='immutable-test',
                                status='approved', payload={}))
        s.commit()
    return suite_id


def test_evidence_endpoint_still_returns_raw_png(clients):
    from sqlalchemy.orm import sessionmaker

    from testpilot import db

    factory = sessionmaker(db.engine(), expire_on_commit=False)
    suite_id = _seed_suite(factory, db)
    evidence_id = '00000000-0000-0000-0000-0000000000dd'
    payload = b'\x89PNG\r\n\x1a\n' + b'raw'
    try:
        with factory() as s:
            s.add(db.EvidenceRow(id=evidence_id, suite_id=suite_id, content=payload))
            s.commit()
        _, c = clients
        response = c.get(f'/api/v1/ai/evidence/{evidence_id}')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('image/png')
        assert response.content == payload, 'эндпоинт отдаёт те же байты, created_at в ответ не попадает'
    finally:
        with factory() as s:
            row = s.get(db.EvidenceRow, evidence_id)
            if row is not None:
                s.delete(row)
            suite = s.get(db.AISuiteRow, suite_id)
            if suite is not None:
                s.delete(suite)
            package = s.get(db.PackageRow, 'pkg-immutable2')
            if package is not None:
                s.delete(package)
            s.commit()
