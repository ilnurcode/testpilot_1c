"""A2 smoke: backup/restore фикстур на двух одноразовых tmpfs-PostgreSQL стендах.

Рабочая БД не участвует: только собственные fixture credentials/записи.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

from isolation_smoke import Instance, SmokeError, clean_env

ROOT = Path(__file__).resolve().parent.parent


def run_checked(argv: list[str], *, what: str, expect: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=clean_env())
    if result.returncode != expect:
        raise SmokeError(f'{what}: код {result.returncode}, ожидался {expect}: {(result.stderr or result.stdout).strip()[-500:]}')
    return result


def seed(instance: Instance) -> None:
    engine = create_engine(instance.dsn)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO ti_packages(id,checksum,configuration_id,payload) "
                "VALUES ('pkg-a2', repeat('0',64), 'cfg-a2', '{}'::json)"
            ))
            connection.execute(text(
                "INSERT INTO ti_ai_suites(id,package_id,project_id,status,payload,created_at) "
                "VALUES ('suite-a2','pkg-a2','project-a2','completed','{}'::json,'2026-01-01T00:00:00Z')"
            ))
            connection.execute(text(
                "INSERT INTO ti_ai_evidence(id,suite_id,content) VALUES (:id,:suite,:content)"
            ), {'id': 'evidence-a2', 'suite': 'suite-a2', 'content': b'\x89PNG-a2-fixture'})
            connection.execute(text(
                "INSERT INTO impact_projects(id,name,description,created_by,created_at,next_number) "
                "VALUES ('project-a2','A2 fixture','','test','2026-01-01T00:00:00Z',1)"
            ))
    finally:
        engine.dispose()


def backup(instance: Instance, dump_dir: Path) -> Path:
    args = [sys.executable, str(ROOT / 'scripts' / 'pg_backup.py'), 'backup',
            '--project', instance.project, '--compose-file', str(instance.compose_file),
            '--env-file', str(instance.env_file), '--expect-db', instance.database,
            '--instance-token', instance.token, '--db-user', instance.user, '--dump-dir', str(dump_dir)]
    run_checked(args, what='backup')
    backups = sorted(path for path in dump_dir.glob('testpilot-*') if path.is_dir())
    if len(backups) != 1:
        raise SmokeError('backup не создал ровно одну директорию backup')
    if list(dump_dir.glob('*.tmp')):
        raise SmokeError('backup оставил .tmp файл')
    if not (backups[0] / 'dump.sql').is_file() or not (backups[0] / 'manifest.json').is_file():
        raise SmokeError('backup не создал dump.sql + manifest.json')
    json.loads((backups[0] / 'manifest.json').read_text(encoding='utf-8'))
    return backups[0]


def restore(instance: Instance, dump: Path, *, expect: int = 0) -> None:
    args = [sys.executable, str(ROOT / 'scripts' / 'pg_backup.py'), 'restore',
            '--dump', str(dump), '--yes', '--project', instance.project,
            '--compose-file', str(instance.compose_file), '--env-file', str(instance.env_file),
            '--expect-db', instance.database, '--instance-token', instance.token,
            '--db-user', instance.user]
    run_checked(args, what='restore', expect=expect)


def target_is_empty_with_marker(instance: Instance) -> bool:
    engine = create_engine(instance.dsn)
    try:
        with engine.connect() as connection:
            tables = connection.execute(text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )).scalar()
            marker = connection.execute(text(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
            )).scalar()
        return tables == 0 and marker == instance.token
    finally:
        engine.dispose()


def bad_manifest(source: Path, workdir: Path, name: str, mutate) -> Path:
    target = workdir / name
    shutil.copytree(source, target)
    manifest_path = target / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return target


def main() -> int:
    if os.environ.get('TP_ALLOW_DISPOSABLE_STACKS') != '1':
        print('FAIL  запуск A2 smoke требует TP_ALLOW_DISPOSABLE_STACKS=1 (разрешение владельца)', file=sys.stderr)
        return 2
    used_ports: set[int] = set()
    instances: list[Instance] = []
    workdir = Path(tempfile.mkdtemp(prefix='tp-a2-'))
    proof: dict = {'result': 'FAIL'}
    status = 1
    try:
        source = Instance(used_ports)
        target = Instance(used_ports)
        instances = [source, target]
        source.prepare()
        target.prepare(migrate=False)
        seed(source)
        dump = backup(source, workdir)
        proof['dump'] = {'name': dump.name, 'size': (dump / 'dump.sql').stat().st_size}

        missing_payload = bad_manifest(dump, workdir, 'missing-payload', lambda m: m.pop('payload_md5'))
        restore(target, missing_payload, expect=1)
        if not target_is_empty_with_marker(target):
            raise SmokeError('после отказа missing payload target не пуст/потерян marker')
        proof['missing_payload_refused_empty_target'] = True

        missing_evidence = bad_manifest(dump, workdir, 'missing-evidence', lambda m: m.pop('evidence_sha256'))
        restore(target, missing_evidence, expect=1)
        if not target_is_empty_with_marker(target):
            raise SmokeError('после отказа missing evidence target не пуст/потерян marker')
        proof['missing_evidence_refused_empty_target'] = True

        def wrong_counts(manifest: dict) -> None:
            manifest['counts']['impact_projects'] += 1

        wrong = bad_manifest(dump, workdir, 'wrong-counts', wrong_counts)
        restore(target, wrong, expect=1)
        if not target_is_empty_with_marker(target):
            raise SmokeError('после отказа wrong counts target не пуст/потерян marker (COMMIT до проверки)')
        proof['wrong_counts_refused_empty_target'] = True

        restore(target, dump)
        proof['restore'] = 'verified'
        restore(source, dump, expect=1)
        proof['nonempty_target_refused'] = True

        corrupted = workdir / 'corrupted'
        shutil.copytree(dump, corrupted)
        with open(corrupted / 'dump.sql', 'r+b') as handle:
            handle.truncate(max(1, (corrupted / 'dump.sql').stat().st_size // 2))
        restore(target, corrupted, expect=1)
        proof['corrupt_dump_refused'] = True

        partial = workdir / 'partial'
        shutil.copytree(dump, partial)
        (partial / 'manifest.json').unlink()
        restore(target, partial, expect=1)
        proof['partial_manifest_refused'] = True

        engine = create_engine(target.dsn)
        try:
            with engine.connect() as connection:
                content = connection.execute(text("SELECT encode(content,'hex') FROM ti_ai_evidence WHERE id='evidence-a2'")).scalar()
                project = connection.execute(text("SELECT md5(row_to_json(t)::text) FROM impact_projects t WHERE id='project-a2'")).scalar()
                suite = connection.execute(text("SELECT md5(row_to_json(t)::text) FROM ti_ai_suites t WHERE id='suite-a2'")).scalar()
        finally:
            engine.dispose()
        if content != b'\x89PNG-a2-fixture'.hex() or not project or not suite:
            raise SmokeError('после restore content/payload не совпадают')
        proof['content_and_payload_verified'] = True
        proof['result'] = 'PASS'
        status = 0
    except (SmokeError, OSError, ValueError) as error:
        proof['error'] = str(error)
    finally:
        proof['cleanup'] = []
        for instance in instances:
            try:
                proof['cleanup'].append(instance.cleanup())
            except Exception as error:
                proof['cleanup'].append({'project': instance.project, 'cleaned': False, 'reason': str(error)})
        print(json.dumps(proof, ensure_ascii=False, indent=2))
        shutil.rmtree(workdir, ignore_errors=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
