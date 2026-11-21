"""A1 smoke: два уникальных одноразовых PostgreSQL-стенда без чужих ресурсов.

- Уникальные project/DB/port/nonce на запуск; PG на tmpfs, без named/anonymous volumes.
- Никаких down -v / volume rm/prune. Cleanup — только собственные контейнеры/сеть
  после повторной проверки nonce/labels/container id; при сомнении не удаляем ничего.
- Приложения, 1С, LLM и рабочая БД не затрагиваются; внешних сетевых вызовов нет.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
IMAGE = 'postgres:17-alpine'
OWNER = 'testpilot.disposable.owner'
NONCE = 'testpilot.disposable.nonce'
MARKER_SQL = "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database()"


class SmokeError(RuntimeError):
    pass


def clean_env() -> dict:
    keys = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'DOCKER_HOST')
    return {key: os.environ[key] for key in keys if os.environ.get(key)}


def run(argv: list[str], *, what: str, timeout: int = 300) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=clean_env())
    if result.returncode != 0:
        # Не печатаем argv/stderr: там бывают значения env-файла.
        raise SmokeError(f'{what}: код {result.returncode}')
    return result.stdout


def free_port(used: set[int]) -> int:
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port not in used:
            used.add(port)
            return port
    raise SmokeError('не удалось выделить уникальный свободный порт')


class Instance:
    def __init__(self, used_ports: set[int]) -> None:
        self.nonce = secrets.token_hex(8)
        suffix = self.nonce[:12]
        self.project = f'tpa1-{suffix}'
        self.database = f'tp_a1_{suffix}'
        self.user = 'tp_a1_admin'
        self.password = secrets.token_urlsafe(24)
        self.token = f'a1.{self.nonce}.{secrets.token_hex(6)}'
        self.port = free_port(used_ports)
        self.workdir = Path(tempfile.mkdtemp(prefix=f'tp-a1-{suffix}-'))
        self.compose_file = self.workdir / 'compose.yaml'
        self.env_file = self.workdir / 'stack.env'
        self.container_id = ''
        self.revision = ''
        self.dsn = f'postgresql+psycopg://{self.user}:{self.password}@127.0.0.1:{self.port}/{self.database}'

    def compose(self, *args: str) -> str:
        return run(['docker', 'compose', '-f', str(self.compose_file), '-p', self.project,
                    '--env-file', str(self.env_file), *args],
                   what=f'{self.project}: {args[0]}')

    def prepare(self, *, migrate: bool = True) -> None:
        self.env_file.write_text(
            f'POSTGRES_USER={self.user}\nPOSTGRES_PASSWORD={self.password}\n'
            f'POSTGRES_DB={self.database}\nDB_PORT={self.port}\n', encoding='utf-8')
        self.compose_file.write_text(
            f"""services:
  db:
    image: {IMAGE}
    environment:
      POSTGRES_USER: ${{POSTGRES_USER}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
      POSTGRES_DB: ${{POSTGRES_DB}}
    ports:
      - "127.0.0.1:${{DB_PORT}}:5432"
    tmpfs:
      - /var/lib/postgresql/data
    labels:
      {OWNER}: a1-harness
      {NONCE}: {self.nonce}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${{POSTGRES_USER}} -d $${{POSTGRES_DB}}"]
      interval: 1s
      timeout: 2s
      retries: 30
""", encoding='utf-8')
        if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True,
                          env=clean_env()).returncode != 0:
            raise SmokeError(f'локальный образ {IMAGE} отсутствует; pull не выполняется')
        self.compose('up', '-d', '--wait')
        self.container_id = self._owned_container_id()
        self._assert_no_volumes()
        self._set_marker()
        if migrate:
            self._migrate()

    def _owned_container_id(self) -> str:
        output = run(['docker', 'ps', '-a', '--filter', f'label=com.docker.compose.project={self.project}',
                      '--format', '{{.ID}}|{{.Label "com.docker.compose.service"}}|'
                                  f'{{{{.Label "{NONCE}"}}}}'], what='ownership контейнеров')
        rows = [line.split('|') for line in output.splitlines() if line]
        if len(rows) != 1 or rows[0][1] != 'db' or rows[0][2] != self.nonce:
            raise SmokeError(f'{self.project}: ownership контейнеров не подтверждена')
        return rows[0][0]

    def _assert_no_volumes(self) -> None:
        volumes = run(['docker', 'volume', 'ls', '--filter', f'label=com.docker.compose.project={self.project}',
                       '--format', '{{.Name}}'], what='volumes').strip()
        if volumes:
            raise SmokeError(f'{self.project}: созданы volumes, ожидался tmpfs')

    def _set_marker(self) -> None:
        engine = create_engine(self.dsn)
        try:
            with engine.begin() as connection:
                name = connection.execute(text('SELECT current_database()')).scalar()
                if name != self.database:
                    raise SmokeError(f'{self.project}: БД {name!r} не совпадает с ожидаемой')
                if not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', self.token):
                    raise SmokeError(f'{self.project}: недопустимый маркер instance')
                connection.execute(text(f"COMMENT ON DATABASE \"{self.database}\" IS '{self.token}'"))
            with engine.connect() as connection:
                marker = connection.execute(text(MARKER_SQL)).scalar()
            if marker != self.token:
                raise SmokeError(f'{self.project}: маркер владельца БД не подтверждён')
        finally:
            engine.dispose()

    def _migrate(self) -> None:
        engine = create_engine(self.dsn)
        try:
            config = Config(str(ROOT / 'alembic.ini'))
            with engine.begin() as connection:
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
            with engine.connect() as connection:
                self.revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        finally:
            engine.dispose()

    def owns(self) -> bool:
        try:
            output = run(['docker', 'ps', '-a', '--filter', f'label=com.docker.compose.project={self.project}',
                          '--format', '{{.ID}}|{{.Label "com.docker.compose.service"}}|'
                                      f'{{{{.Label "{NONCE}"}}}}'], what='ownership')
        except SmokeError:
            return False
        rows = [line.split('|') for line in output.splitlines() if line]
        return (len(rows) == 1 and rows[0][0] == self.container_id
                and rows[0][1] == 'db' and rows[0][2] == self.nonce)

    def marker_ok(self) -> bool:
        engine = create_engine(self.dsn)
        try:
            with engine.connect() as connection:
                return connection.execute(text(MARKER_SQL)).scalar() == self.token
        except Exception:
            return False
        finally:
            engine.dispose()

    def cleanup(self) -> dict:
        if not (self.owns() and self.marker_ok()):
            return {'project': self.project, 'cleaned': False,
                    'reason': 'ownership не подтверждена; ресурсы не удалялись'}
        self.compose('down', '--remove-orphans')
        return {'project': self.project, 'cleaned': True}

    def proof(self) -> dict:
        return {'project': self.project, 'database': self.database, 'port': self.port,
                'nonce': self.nonce, 'alembic': self.revision, 'marker_ok': self.marker_ok()}


def wrong_identity_denied(instance: Instance) -> bool:
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'target_identity.py'),
         '--project', instance.project, '--expect-db', 'definitely-wrong-db',
         '--compose-file', str(instance.compose_file), '--env-file', str(instance.env_file),
         '--services', 'db', '--json'],
        capture_output=True, text=True, timeout=120, env=clean_env())
    return result.returncode == 2


def run_migration_tests(instance: Instance) -> None:
    env = {**clean_env(), 'DATABASE_URL': '',
           'TP_TEST_DATABASE_URL': instance.dsn,
           'TP_TEST_EXPECT_DB': instance.database,
           'TP_TEST_INSTANCE_TOKEN': instance.token}
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_schema_migrations.py', '-q'],
                            capture_output=True, text=True, timeout=300, env=env, cwd=ROOT)
    if result.returncode != 0:
        raise SmokeError('migration head/drift tests не прошли')


def main() -> int:
    if os.environ.get('TP_ALLOW_DISPOSABLE_STACKS') != '1':
        print('FAIL  запуск одноразовых стендов требует TP_ALLOW_DISPOSABLE_STACKS=1 (разрешение владельца)',
              file=sys.stderr)
        return 2
    instances: list[Instance] = []
    proof: dict = {'instances': [], 'result': 'FAIL'}
    status = 1
    try:
        used_ports: set[int] = set()
        for _ in range(2):
            instance = Instance(used_ports)
            instances.append(instance)
            instance.prepare()
            proof['instances'].append(instance.proof())
        if len({item.project for item in instances}) != 2:
            raise SmokeError('проекты не уникальны')
        if len({item.database for item in instances}) != 2:
            raise SmokeError('БД не уникальны')
        if len({item.port for item in instances}) != 2:
            raise SmokeError('порты не уникальны')
        for instance in instances:
            if not instance.owns() or not instance.marker_ok():
                raise SmokeError(f'{instance.project}: ownership/marker потеряны')
            if not wrong_identity_denied(instance):
                raise SmokeError(f'{instance.project}: неверная identity не отклонена')
        run_migration_tests(instances[0])
        proof['migration_tests'] = 'passed'
        proof['result'] = 'PASS'
        status = 0
    except (SmokeError, subprocess.SubprocessError) as error:
        proof['error'] = str(error)
    finally:
        cleanup = []
        for instance in instances:
            try:
                result = instance.cleanup()
            except Exception as error:
                result = {'project': instance.project, 'cleaned': False, 'reason': str(error)}
            cleanup.append(result)
            if result.get('cleaned'):
                shutil.rmtree(instance.workdir, ignore_errors=True)
        proof['cleanup'] = cleanup
        print(json.dumps(proof, ensure_ascii=False, indent=2))
        leftover = [item['project'] for item in cleanup if not item.get('cleaned')]
        if leftover:
            print(f'WARN  ресурсы не удалены (ownership не подтверждена): {", ".join(leftover)}', file=sys.stderr)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
