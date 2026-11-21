"""Read-only проверка идентичности compose-стенда и вывод его адресов.

Все вызовы docker compose получают явный compose file, проект и (при наличии) env-file;
cwd и рабочий `.env` не используются. Ничего не создаёт и не удаляет.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SERVICES = {'db': 5432, 'impact': 8001, 'intelligence': 8002, 'web': 8000}


class IdentityError(RuntimeError):
    pass


def clean_env() -> dict:
    keys = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'DOCKER_HOST')
    return {key: os.environ[key] for key in keys if os.environ.get(key)}


def _run(argv: list[str], *, what: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=clean_env())
    if result.returncode != 0:
        # Не печатаем argv/stderr: там могут оказаться пути env-файлов и служебные значения.
        raise IdentityError(f'{what}: docker завершился с кодом {result.returncode}')
    return result.stdout


def _compose_base(project: str, compose_file: str, env_file: str | None) -> list[str]:
    argv = ['docker', 'compose', '-f', compose_file, '-p', project]
    if env_file:
        argv += ['--env-file', env_file]
    return argv


def _containers(project: str) -> dict[str, str]:
    output = _run(['docker', 'ps', '-a', '--filter', f'label=com.docker.compose.project={project}',
                   '--format', '{{.Names}}|{{.Label "com.docker.compose.project"}}|'
                               '{{.Label "com.docker.compose.service"}}|{{.State}}'],
                  what=f'список контейнеров {project}')
    found: dict[str, str] = {}
    for line in output.splitlines():
        name, label_project, service, state = (line.split('|') + [''] * 4)[:4]
        if label_project != project:
            raise IdentityError(f'контейнер {name} помечен проектом {label_project!r}, а не {project!r}')
        found[service] = state
    return found


def _published_port(base: list[str], service: str, container_port: int) -> int:
    output = _run([*base, 'port', service, str(container_port)], what=f'порт {service}/{container_port}').strip()
    if not output:
        raise IdentityError(f'у сервиса {service} нет публикации порта {container_port}')
    first = output.splitlines()[0]
    host_port = first.rsplit(':', 1)[-1]
    if not host_port.isdigit():
        raise IdentityError(f'не удалось разобрать порт сервиса {service}')
    return int(host_port)


def _database_identity(base: list[str], project: str) -> str:
    query = 'select current_database()'
    output = _run([*base, 'exec', '-T', 'db', 'sh', '-c',
                   f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{query}"'],
                  what=f'identity БД проекта {project}').strip()
    if not output:
        raise IdentityError(f'не удалось прочитать current_database() в проекте {project}')
    return output


def collect(project: str, expect_db: str, compose_file: str, env_file: str | None,
            services: tuple[str, ...] = tuple(SERVICES)) -> dict:
    base = _compose_base(project, compose_file, env_file)
    states = _containers(project)
    for service in services:
        if service not in states:
            raise IdentityError(f'проект {project!r} не содержит сервис {service!r}')
        if states[service] != 'running':
            raise IdentityError(f'сервис {project}/{service} не running: {states[service]!r}')
    db_name = _database_identity(base, project)
    if db_name != expect_db:
        raise IdentityError(f'target identity mismatch: БД {db_name!r}, ожидалась {expect_db!r}')
    ports = {service: _published_port(base, service, SERVICES[service]) for service in services}
    data = {'project': project, 'database': db_name, 'ports': ports, 'db_host_port': ports.get('db')}
    if 'web' in ports:
        data['web_url'] = f'http://127.0.0.1:{ports["web"]}'
        data['impact_url'] = f'http://127.0.0.1:{ports["impact"]}'
        data['intelligence_url'] = f'http://127.0.0.1:{ports["intelligence"]}'
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only identity и порты compose-стенда')
    parser.add_argument('--project', required=True)
    parser.add_argument('--expect-db', required=True)
    parser.add_argument('--compose-file', required=True)
    parser.add_argument('--env-file', default=None)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--services', default=','.join(SERVICES),
                        help='comma-separated subset: db,impact,intelligence,web')
    args = parser.parse_args(argv)
    services = tuple(item.strip() for item in args.services.split(',') if item.strip())
    unknown = [item for item in services if item not in SERVICES]
    if unknown or not services:
        print(f'FAIL  неизвестные сервисы: {unknown or services}', file=sys.stderr)
        return 2
    try:
        data = collect(args.project, args.expect_db, args.compose_file, args.env_file, services)
    except IdentityError as error:
        print(f'FAIL  target identity: {error}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f'PROJECT={data["project"]}')
        print(f'EXPECT_DB={data["database"]}')
        if 'web' in data:
            print(f'WEB={data["web_url"]}')
            print(f'IMPACT={data["impact_url"]}')
            print(f'INTEL={data["intelligence_url"]}')
        print(f'DB_PORT={data["db_host_port"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
