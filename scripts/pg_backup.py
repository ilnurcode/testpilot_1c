"""Атомарный backup/restore PostgreSQL на едином snapshot (A2).

Единый механизм для одноразовых фикстур и ручного read-only backup:
- источник идентифицируется positive marker (DB name + COMMENT ON DATABASE token);
- pg_export_snapshot удерживается до конца pg_dump и metadata-запросов;
- dump+manifest публикуются одной директорией через атомарный rename;
- restore только в пустую БД с marker, psql --single-transaction + ON_ERROR_STOP;
- при ошибке/несовпадении hashes/counts/payload — FAIL, без stamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNTS = ('impact_projects', 'impact_uploads', 'ti_ai_suites', 'ti_ai_evidence', 'code_audit')


class BackupError(RuntimeError):
    pass


class SnapshotSession:
    """psql-сессия, удерживающая экспортированный snapshot."""

    def __init__(self, args):
        self.args = args
        self.process = None
        self.snapshot = ''

    def __enter__(self):
        argv = self.args.compose_prefix + ['exec', '-T', 'db', 'psql', '-X', '-q', '-A', '-t',
                                           '-v', 'ON_ERROR_STOP=1', '-U', self.args.db_user,
                                           self.args.expect_db]
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1,
                                        env=clean_env())
        self._send('BEGIN ISOLATION LEVEL REPEATABLE READ;')
        self._send('SELECT pg_export_snapshot();')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stdout.readline().strip()
            if line:
                self.snapshot = line
                return self
        raise BackupError('не удалось получить pg_export_snapshot')

    def _send(self, sql: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise BackupError('snapshot-сессия не запущена')
        self.process.stdin.write(sql + '\n')
        self.process.stdin.flush()

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.process and self.process.poll() is None:
                self._send('COMMIT;')
        except Exception:
            pass
        finally:
            if self.process:
                try:
                    self.process.stdin.close()
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def clean_env() -> dict:
    keys = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'DOCKER_HOST')
    return {key: os.environ[key] for key in keys if os.environ.get(key)}


def compose_prefix(args) -> list[str]:
    argv = ['docker', 'compose', '-f', args.compose_file, '-p', args.project]
    if args.env_file:
        argv += ['--env-file', args.env_file]
    return argv


def run_psql(args, sql: str) -> str:
    result = subprocess.run(compose_prefix(args) + ['exec', '-T', 'db', 'psql', '-X', '-q', '-A', '-t',
                                                    '-v', 'ON_ERROR_STOP=1', '-U', args.db_user,
                                                    args.expect_db, '-c', sql],
                            capture_output=True, text=True, timeout=120, env=clean_env())
    if result.returncode != 0:
        raise BackupError(f'psql завершился кодом {result.returncode}')
    return result.stdout.strip()


def identity(args) -> None:
    actual = run_psql(args, 'SELECT current_database()')
    marker = run_psql(args, "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database()")
    if actual != args.expect_db or marker != args.instance_token:
        raise BackupError('target identity mismatch: database/marker не совпадают с ожидаемыми')


def snapshot_metadata(args, snapshot: str) -> dict:
    query_tail = f"""SET TRANSACTION SNAPSHOT '{snapshot}';
SELECT 'count:' || t || ':' || c FROM (
  SELECT 'impact_projects' AS t, count(*) AS c FROM impact_projects UNION ALL
  SELECT 'impact_uploads', count(*) FROM impact_uploads UNION ALL
  SELECT 'ti_ai_suites', count(*) FROM ti_ai_suites UNION ALL
  SELECT 'ti_ai_evidence', count(*) FROM ti_ai_evidence UNION ALL
  SELECT 'code_audit', count(*) FROM code_audit
) counted;
SELECT 'rev:' || version_num FROM alembic_version;
SELECT 'project:' || id || '|' || md5(row_to_json(t)::text) FROM impact_projects t;
SELECT 'suite:' || id || '|' || md5(row_to_json(t)::text) FROM ti_ai_suites t;
SELECT 'ev:' || id || '|' || encode(content, 'hex') FROM ti_ai_evidence;
SELECT '__done__';"""
    process = subprocess.Popen(compose_prefix(args) + ['exec', '-T', 'db', 'psql', '-X', '-q', '-A', '-t',
                                                        '-v', 'ON_ERROR_STOP=1', '-U', args.db_user,
                                                        args.expect_db],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, env=clean_env())
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ;\n')
    process.stdin.write(query_tail + '\n')
    process.stdin.flush()
    counts: dict[str, int] = {}
    payloads = {'projects': {}, 'suites': {}}
    evidence = {}
    revision = ''
    done = False
    for line in process.stdout:
        line = line.rstrip('\r\n')
        if not line:
            continue
        if line == '__done__':
            done = True
            break
        if line.startswith('count:'):
            _, table, value = line.split(':', 2)
            counts[table] = int(value)
        elif line.startswith('rev:'):
            revision = line[4:]
        elif line.startswith('project:'):
            item_id, digest = line[8:].split('|', 1)
            payloads['projects'][item_id] = digest
        elif line.startswith('suite:'):
            item_id, digest = line[6:].split('|', 1)
            payloads['suites'][item_id] = digest
        elif line.startswith('ev:'):
            item_id, hex_content = line[3:].split('|', 1)
            evidence[item_id] = hashlib.sha256(bytes.fromhex(hex_content)).hexdigest()
    if process.poll() is not None:
        error = process.stderr.read() if process.stderr else ''
        raise BackupError(f'psql metadata завершился кодом {process.returncode}: {error[:300]}')
    try:
        process.stdin.write('COMMIT;\n')
        process.stdin.flush()
        process.stdin.close()
    except OSError as error:
        detail = process.stderr.read() if process.stderr else ''
        raise BackupError(f'psql metadata stdin: {error}: {detail[:300]}') from error
    error = process.stderr.read() if process.stderr else ''
    code = process.wait(timeout=30)
    if code != 0 or not done:
        raise BackupError(f'psql metadata завершился кодом {code}: {error[:300]}')
    return {'counts': counts, 'alembic_revision': revision,
            'payload_md5': payloads, 'evidence_sha256': evidence}

def dump_stream(args, snapshot: str, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(target, 'wb') as handle:
        process = subprocess.Popen(compose_prefix(args) + ['exec', '-T', 'db', 'pg_dump',
                                                          '--snapshot', snapshot,
                                                          '-U', args.db_user, args.expect_db],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=clean_env())
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        error = process.stderr.read() if process.stderr else b''
        code = process.wait()
        handle.flush()
        os.fsync(handle.fileno())
    if code != 0:
        raise BackupError(f'pg_dump завершился кодом {code}: {error[:120].decode("utf-8", "replace")}')
    return size, digest.hexdigest()


def backup(args) -> int:
    if args.manual:
        if not args.confirm_project or args.confirm_project != args.project:
            raise BackupError('ручной backup требует --confirm-project, совпадающий с --project')
    identity(args)
    dump_dir = Path(args.dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    name = f'testpilot-{stamp}-{os.getpid()}-{os.urandom(3).hex()}'
    final = dump_dir / name
    temp_dir = dump_dir / f'.{name}.tmp'
    temp_dir.mkdir()
    dump_file = temp_dir / 'dump.sql'
    manifest_file = temp_dir / 'manifest.json'
    try:
        with SnapshotSession(args) as snapshot:
            size, sha = dump_stream(args, snapshot.snapshot, dump_file)
            metadata = snapshot_metadata(args, snapshot.snapshot)
        manifest = {
            'format': 'testpilot-backup-1', 'dump': 'dump.sql', 'size': size, 'sha256': sha,
            'database': args.expect_db, 'instance_token': args.instance_token,
            'created_at': datetime.now(timezone.utc).isoformat(), **metadata,
        }
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        for path in (dump_file, manifest_file):
            with open(path, 'rb+') as handle:
                os.fsync(handle.fileno())
        os.replace(temp_dir, final)
        if os.name != 'nt':
            dir_fd = os.open(dump_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    print(f'OK  backup: {final} (dump {size} байт, sha256 {sha[:12]}…)')
    return 0


def _is_hex(value: str, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def _validate_manifest(manifest: dict) -> None:
    if manifest.get('format') != 'testpilot-backup-1' or manifest.get('dump') != 'dump.sql':
        raise BackupError('манифест: неверный format/dump')
    if not isinstance(manifest.get('size'), int) or isinstance(manifest.get('size'), bool) or manifest['size'] <= 0:
        raise BackupError('манифест: неверный size')
    if not _is_hex(manifest.get('sha256'), 64):
        raise BackupError('манифест: неверный sha256')
    if not isinstance(manifest.get('database'), str) or not manifest['database']:
        raise BackupError('манифест: нет database')
    if not isinstance(manifest.get('instance_token'), str) or not manifest['instance_token']:
        raise BackupError('манифест: нет instance_token')
    revision = manifest.get('alembic_revision')
    if not isinstance(revision, str) or not __import__('re').fullmatch(r'[A-Za-z0-9_.-]{1,64}', revision):
        raise BackupError('манифест: неверный alembic_revision')
    counts = manifest.get('counts')
    if not isinstance(counts, dict) or set(counts) != set(COUNTS):
        raise BackupError('манифест: неверный набор counts')
    for value in counts.values():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BackupError('манифест: counts должны быть неотрицательными целыми')
    payload = manifest.get('payload_md5')
    if not isinstance(payload, dict) or set(payload) != {'projects', 'suites'}:
        raise BackupError('манифест: нет payload_md5 projects/suites')
    for section in ('projects', 'suites'):
        if not isinstance(payload[section], dict):
            raise BackupError(f'манифест: payload_md5[{section}] не словарь')
        for key, value in payload[section].items():
            if not isinstance(key, str) or not _is_hex(value, 32):
                raise BackupError(f'манифест: неверный md5 в payload_md5[{section}]')
    evidence = manifest.get('evidence_sha256')
    if not isinstance(evidence, dict):
        raise BackupError('манифест: нет evidence_sha256')
    for key, value in evidence.items():
        if not isinstance(key, str) or not _is_hex(value, 64):
            raise BackupError('манифест: неверный sha256 в evidence_sha256')


def _digest_pairs(mapping: dict[str, str]) -> str:
    text = ','.join(f'{key}:{mapping[key]}' for key in sorted(mapping))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _verification_sql(manifest: dict) -> str:
    counts = manifest['counts']
    expected_payload = {section: _digest_pairs(manifest['payload_md5'][section]) for section in ('projects', 'suites')}
    expected_evidence = _digest_pairs(manifest['evidence_sha256'])
    checks = []
    for table in COUNTS:
        checks.append(f"IF (SELECT count(*) FROM {table}) IS DISTINCT FROM {counts[table]} THEN RAISE EXCEPTION 'counts mismatch: {table}'; END IF;")
    checks.append(f"IF (SELECT version_num FROM alembic_version) IS DISTINCT FROM '{manifest['alembic_revision']}' THEN RAISE EXCEPTION 'alembic revision mismatch'; END IF;")
    checks.append("IF (SELECT encode(sha256(convert_to(coalesce(string_agg(id || ':' || md5(row_to_json(t)::text), ',' ORDER BY id), ''), 'UTF8')), 'hex') FROM impact_projects t) IS DISTINCT FROM '%s' THEN RAISE EXCEPTION 'payload projects mismatch'; END IF;" % expected_payload['projects'])
    checks.append("IF (SELECT encode(sha256(convert_to(coalesce(string_agg(id || ':' || md5(row_to_json(t)::text), ',' ORDER BY id), ''), 'UTF8')), 'hex') FROM ti_ai_suites t) IS DISTINCT FROM '%s' THEN RAISE EXCEPTION 'payload suites mismatch'; END IF;" % expected_payload['suites'])
    checks.append("IF (SELECT encode(sha256(convert_to(coalesce(string_agg(id || ':' || encode(sha256(content), 'hex'), ',' ORDER BY id), ''), 'UTF8')), 'hex') FROM ti_ai_evidence) IS DISTINCT FROM '%s' THEN RAISE EXCEPTION 'evidence hash mismatch'; END IF;" % expected_evidence)
    return "SET search_path = public;\nDO $tp_verify$ BEGIN " + ' '.join(checks) + ' END $tp_verify$;\n'


def _load_backup(args) -> tuple[Path, Path, dict]:
    path = Path(args.dump)
    if path.is_dir():
        dump = path / 'dump.sql'
        manifest_path = path / 'manifest.json'
    else:
        dump = path
        manifest_path = path.with_suffix('.manifest.json')
    if not dump.is_file() or not manifest_path.is_file():
        raise BackupError('нужны dump.sql и manifest.json (директория backup)')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise BackupError(f'манифест не читается: {error}') from error
    if not isinstance(manifest, dict):
        raise BackupError('манифест должен быть объектом')
    _validate_manifest(manifest)
    digest = hashlib.sha256()
    size = 0
    with open(dump, 'rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if size != manifest['size'] or digest.hexdigest() != manifest['sha256']:
        raise BackupError('дамп не совпадает с манифестом (size/sha256)')
    return dump, manifest_path, manifest

def restore(args) -> int:
    if not args.yes:
        raise BackupError('восстановление требует --yes')
    dump, _manifest_path, manifest = _load_backup(args)
    identity(args)
    tables = int(run_psql(args, "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"))
    if tables != 0:
        raise BackupError('target не пуст: восстановление запрещено')
    verification = _verification_sql(manifest).encode('utf-8')
    process = subprocess.Popen(compose_prefix(args) + ['exec', '-T', 'db', 'psql', '-X', '-q',
                                                      '--single-transaction', '-v', 'ON_ERROR_STOP=1',
                                                      '-f', '-', '-U', args.db_user, args.expect_db],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=clean_env())
    assert process.stdin is not None
    write_error = None
    try:
        with open(dump, 'rb') as handle:
            while chunk := handle.read(1024 * 1024):
                process.stdin.write(chunk)
        process.stdin.write(verification)
    except (BrokenPipeError, OSError) as error:
        write_error = error
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
    process.stdin = None  # communicate() must not flush the already closed pipe
    try:
        _, error = process.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise BackupError('restore timeout: transaction отменена')
    if process.returncode != 0:
        detail = (error or b'')[:300].decode('utf-8', 'replace')
        raise BackupError(f'psql restore завершился кодом {process.returncode}: {detail}')
    if write_error is not None:
        raise BackupError(f'не удалось отправить dump в psql: {write_error}')
    print(f"OK  restore: {dump.name}; counts={manifest['counts']}; alembic={manifest['alembic_revision']}")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Атомарный backup/restore PostgreSQL на едином snapshot')
    sub = parser.add_subparsers(dest='mode', required=True)
    for name in ('backup', 'restore'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--project', required=True)
        cmd.add_argument('--compose-file', required=True)
        cmd.add_argument('--env-file', default=None)
        cmd.add_argument('--expect-db', required=True)
        cmd.add_argument('--instance-token', required=True)
        cmd.add_argument('--db-user', required=True)
        if name == 'backup':
            cmd.add_argument('--dump-dir', default=str(ROOT / '.runtime' / 'backups'))
            cmd.add_argument('--manual', action='store_true')
            cmd.add_argument('--confirm-project', default=None)
        else:
            cmd.add_argument('--dump', required=True)
            cmd.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    args.compose_prefix = compose_prefix(args)
    try:
        return backup(args) if args.mode == 'backup' else restore(args)
    except (BackupError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'FAIL  {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
