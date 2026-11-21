"""Контракт запуска TestPilot: обычный старт не меняет схему и не трогает чужие ресурсы.

Проверяются наблюдаемые свойства файлов запуска и поведение лаунчера/хелперов;
сетевые и контейнерные операции не выполняются.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding='utf-8')


def load_launcher():
    spec = importlib.util.spec_from_file_location('testpilot_launcher', ROOT / 'launcher.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --- compose: миграции только явным профилем -------------------------------------------


def test_compose_migrations_are_explicit_and_not_a_startup_dependency():
    compose = read('compose.yaml')
    assert 'profiles: ["init"]' in compose, 'migrate должен запускаться только явным профилем init'
    assert 'service_completed_successfully' not in compose, 'приложения не должны зависеть от migrate'
    assert compose.index('migrate:') < compose.index('profiles: ["init"]')


def test_compose_keeps_parameterized_ports_and_instance_token():
    compose = read('compose.yaml')
    for key in ('TP_DB_HOST_PORT', 'TP_IMPACT_HOST_PORT', 'TP_INTEL_HOST_PORT', 'TP_WEB_HOST_PORT'):
        assert key in compose, f'потеряна параметризация портов {key} (A1)'
    assert 'TP_INSTANCE_TOKEN' in compose


# --- обычный старт: без миграций и без остановки чужих сервисов ------------------------


@pytest.mark.parametrize('script', ['scripts/start.ps1', 'scripts/start-local.sh'])
def test_ordinary_start_never_migrates(script):
    text = read(script).lower()
    for forbidden in ('alembic upgrade', 'alembic downgrade', 'alembic stamp', 'alembic current'):
        assert forbidden not in text, f'{script} не должен выполнять {forbidden}'


def test_ordinary_start_does_not_stop_compose_services():
    assert 'compose stop' not in read('scripts/start.ps1').lower()
    assert 'docker compose stop' not in read('scripts/start-local.sh').lower()


@pytest.mark.parametrize('script', ['scripts/start.ps1', 'scripts/start-local.sh'])
def test_ordinary_start_checks_schema_head_and_refuses(script):
    text = read(script)
    assert 'schema_state.py' in text, f'{script} должен проверять ревизию схемы'
    assert '--require-head' in text, f'{script} должен отказывать при pending/чужой ревизии'


def test_windows_start_writes_identity_receipt():
    text = read('scripts/start.ps1')
    assert 'instance_status.py' in text
    assert '--write-receipt' in text


# --- init/update: явные действия, без downgrade/stamp ----------------------------------


def test_update_requires_confirmation_and_uses_schema_actions():
    text = read('scripts/update.ps1')
    assert '-Confirmed' in text
    assert 'scripts/schema_actions.py' in text and '--update' in text and '--confirmed' in text


def test_init_is_explicit_and_separate():
    text = read('scripts/init.ps1')
    assert 'scripts/schema_actions.py' in text and '--init' in text
    assert 'schema_state.py' in text


@pytest.mark.parametrize('name', ['scripts/schema_actions.py', 'scripts/init.ps1', 'scripts/update.ps1'])
def test_no_downgrade_or_stamp_anywhere_in_schema_actions(name):
    text = read(name).lower()
    assert 'downgrade' not in text, f'{name}: понижение схемы запрещено'
    assert 'stamp' not in text, f'{name}: ручной stamp запрещён'


# --- лаунчер: браузер только после готовности всех сервисов ----------------------------


def test_launcher_probes_all_three_services(monkeypatch):
    launcher = load_launcher()
    probed: list[str] = []
    monkeypatch.setattr(launcher, 'probe', lambda url, timeout=2.0: (probed.append(url), (False, 'test'))[1])

    report = launcher.readiness()

    assert set(report) == {'web', 'impact', 'intelligence'}
    assert len(probed) == 3 and len(set(probed)) == 3


def test_launcher_timeout_exits_nonzero_without_browser(monkeypatch):
    launcher = load_launcher()
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda url: opened.append(url))
    monkeypatch.setattr(launcher, 'readiness', lambda: {name: (False, 'нет') for name in launcher.SERVICE_ORDER})
    monkeypatch.setattr(launcher, 'start_services', lambda: True)
    monkeypatch.setattr(launcher, 'wait_ready', lambda timeout, interval=1.0: {name: (False, 'timeout') for name in launcher.SERVICE_ORDER})
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(launcher, '_pause_if_interactive', lambda: None)
    monkeypatch.setenv('TP_START_TIMEOUT_SECONDS', '1')

    code = launcher.main([])

    assert code != 0
    assert opened == [], 'браузер не должен открываться, если сервисы не готовы'


def test_launcher_start_failure_exits_nonzero_without_browser(monkeypatch):
    launcher = load_launcher()
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda url: opened.append(url))
    monkeypatch.setattr(launcher, 'readiness', lambda: {name: (False, 'нет') for name in launcher.SERVICE_ORDER})
    monkeypatch.setattr(launcher, 'start_services', lambda: False)
    monkeypatch.setattr(launcher, '_pause_if_interactive', lambda: None)

    code = launcher.main([])

    assert code != 0
    assert opened == []


def test_launcher_opens_browser_only_when_all_services_ready(monkeypatch):
    launcher = load_launcher()
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda url: opened.append(url))
    monkeypatch.setattr(launcher, 'readiness', lambda: {name: (True, 'ok') for name in launcher.SERVICE_ORDER})
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)

    code = launcher.main([])

    assert code == 0
    assert opened == [launcher.service_urls()['web']]


# --- каталог данных и отсутствие утечки секретов в вывод хелперов ---------------------


def test_local_env_writes_into_data_dir(tmp_path):
    env = {
        'PATH': os.environ.get('PATH', ''),
        'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
        'TP_DATA_DIR': str(tmp_path),
    }
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'local_env.py')], cwd=str(tmp_path),
                            capture_output=True, text=True, env=env, timeout=120)

    assert result.returncode == 0, result.stderr
    created = tmp_path / '.env'
    assert created.is_file()
    body = created.read_text(encoding='utf-8')
    for key in ('POSTGRES_PASSWORD', 'DATABASE_URL', 'TP_EXPERT_PASSWORD'):
        assert f'{key}=' in body
    # Вывод не содержит значений сгенерированных секретов.
    password = next(line.split('=', 1)[1] for line in body.splitlines() if line.startswith('POSTGRES_PASSWORD='))
    assert password not in result.stdout + result.stderr


def test_instance_status_reports_not_ready_for_unreachable_database_without_leaking_secret(tmp_path):
    env = {
        'PATH': os.environ.get('PATH', ''),
        'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
        'DATABASE_URL': 'postgresql+psycopg://user:sekret-pass@127.0.0.1:1/none',
        'WEB_URL': 'http://127.0.0.1:1',
        'IMPACT_URL': 'http://127.0.0.1:1',
        'INTELLIGENCE_URL': 'http://127.0.0.1:1',
    }
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'instance_status.py'), '--require-ready'],
                            cwd=str(tmp_path), capture_output=True, text=True, env=env, timeout=120)
    elapsed = time.monotonic() - started

    assert elapsed < 60, 'статус не должен зависать на недоступной БД'
    assert result.returncode == 2
    assert 'sekret-pass' not in result.stdout + result.stderr
    assert 'не готов' in (result.stdout + result.stderr).lower()


def test_schema_state_cli_reports_unreachable_database_without_dsn_secret(tmp_path):
    env = {'PATH': os.environ.get('PATH', ''), 'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
           'DATABASE_URL': 'postgresql+psycopg://user:sekret-pass@127.0.0.1:1/none'}
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'schema_state.py'), '--require-head'],
                            cwd=str(tmp_path), capture_output=True, text=True, env=env, timeout=120)
    elapsed = time.monotonic() - started

    assert elapsed < 60, 'проверка схемы не должна зависать на недоступной БД'
    assert result.returncode == 4
    assert 'sekret-pass' not in result.stdout + result.stderr


# --- установщик: единая точка входа first-run (C2) ------------------------------------


def test_installer_first_run_entrypoint_is_the_a3_bootstrap():
    iss = read('installer/testpilot.iss')
    assert '[Run]' in iss, 'после установки нужна явная точка первого запуска'
    run_block = iss.split('[Run]', 1)[1]
    assert 'scripts\\first-run.ps1' in run_block
    assert '-Packaged' in run_block
    assert '{app}\\TestPilot.cmd' not in run_block, '[Run] не должен запускать .cmd, которому нужен .venv'
    icons = iss.split('[Icons]', 1)[1].split('[Run]', 1)[0]
    launch_icons = [line for line in icons.splitlines()
                    if 'TestPilot 1C"' in line and 'Остановить' not in line]
    assert launch_icons and all('first-run.ps1' in line for line in launch_icons)


def test_first_run_bootstrap_is_explicit_idempotent_and_never_migrates_implicitly():
    text = read('scripts/first-run.ps1')
    assert 'init.ps1' in text, 'создание схемы — только явным init.ps1'
    assert 'start.ps1' in text, 'обычный запуск — отдельный start.ps1'
    assert 'update.ps1' in text, 'устаревшая схема требует явного update.ps1 -Confirmed'
    assert 'TP_DATA_DIR' in text and '-Packaged' in text, 'каталог данных вне каталога программы'
    lowered = text.lower()
    for forbidden in ('alembic upgrade', 'alembic downgrade', 'alembic stamp'):
        assert forbidden not in lowered


def test_install_skips_editable_install_for_read_only_package():
    text = read('scripts/install.ps1')
    assert 'Test-WritableDirectory' in text, 'нужна проверка записи в каталог программы'
    assert 'TP_VENV' in read('scripts/windows-common.ps1')
    assert 'PYTHONPATH' in read('scripts/windows-common.ps1'), 'пакет должен импортироваться без установки'


def test_docker_init_and_update_use_the_same_fail_closed_schema_actions():
    """Docker-ветка не обходит проверки состояния прямым `alembic upgrade head`."""
    init = read('scripts/init.ps1')
    update = read('scripts/update.ps1')
    for name, text, verb in (('init.ps1', init, '--init'), ('update.ps1', update, '--update')):
        assert text.count('scripts/schema_actions.py') >= 2, f'{name}: локальная и Docker-ветка должны использовать schema_actions'
        assert text.count(verb) >= 2, f'{name}: {verb} должен быть в обеих ветках'
        assert "run', '--rm', 'migrate')" not in text, f'{name}: голый запуск migrate обходит проверки состояния'
    assert '--confirmed' in update


def test_first_run_does_not_swallow_child_exit_codes():
    text = read('scripts/first-run.ps1')
    for child in ('install.ps1', 'init.ps1', 'start.ps1'):
        assert child in text
    assert text.count('$LASTEXITCODE -ne 0') >= 3, 'first-run должен останавливаться на nonzero дочерних скриптов'


def test_first_run_docker_branch_is_canonical_and_guarded():
    """Docker-вход: явная схема через schema_actions, отказ при pending, без alembic."""
    text = read('scripts/first-run.ps1')
    assert '[switch]$Docker' in text
    assert "'init.ps1') -Docker" in text, 'пустая БД должна инициализироваться явным init -Docker'
    assert 'start-docker.ps1' in text
    assert 'update.ps1 -Confirmed -Docker' in text, 'Docker-ветка подсказывает update с -Docker'
    assert 'update.ps1 -Confirmed\'' in text, 'native-ветка подсказывает update без -Docker'
    lowered = text.lower()
    for forbidden in ('alembic upgrade', 'alembic downgrade', 'alembic stamp'):
        assert forbidden not in lowered


def test_docker_start_checks_head_health_and_writes_receipt():
    text = read('scripts/start-docker.ps1')
    assert "'--profile', 'init', 'run', '--rm', 'migrate'" in text, 'ревизия читается внутри образа'
    assert "'scripts/schema_state.py', '--json'" in text, 'ревизия читается как JSON'
    assert "$schema.state -ne 'ok'" in text, 'несовместимая ревизия — отказ без Ready'
    assert 'schema = $schema' in text, 'квитанция фиксирует ревизию схемы'
    assert "'up', '-d', '--wait', 'web', 'impact', 'intelligence'" in text
    assert "$response.status -eq 'ok'" in text, 'готовность подтверждается фактическим health трёх сервисов'
    assert 'docker-start-receipt.json' in text
    assert 'compose config --format json' in text, 'project в квитанции берётся из compose-конфига'
    assert "else { 'testpilot' }" not in text, 'нельзя подставлять литерал проекта в квитанцию'
    assert 'compose.ps1 stop' in text, 'остановка — guarded compose.ps1 stop'
    lowered = text.lower()
    assert 'alembic' not in lowered
    assert "'down'" not in lowered, 'никакого down без тома в старте'


def test_docker_mode_does_not_require_python_venv_or_env_file():
    text = read('scripts/first-run.ps1')
    guard = text.index('if (-not $Docker)')
    assert text.index("'install.ps1'") > guard, 'установка venv — только в native-ветке'
    assert text.index('Import-LocalEnv') > guard, '.env нужен только native-ветке'
    for name in ('scripts/init.ps1', 'scripts/update.ps1'):
        assert 'if (-not $Docker) { Import-LocalEnv }' in read(name), f'{name}: Docker-ветка не требует .env'


def test_first_run_exit_codes_match_the_wrapper_contract():
    text = read('scripts/first-run.ps1')
    assert 'function Assert-Script' in text
    for child in ("Assert-Script 'install.ps1'", "Assert-Script 'init.ps1'",
                  "Assert-Script 'start.ps1'", "Assert-Script 'start-docker.ps1'"):
        assert child in text, f'отсутствие скрипта должно давать fail-closed: {child}'
    # Предпосылки (Docker/права записи) → код 2, а не общий 1.
    prerequisites = text.split('Assert-Docker', 1)[0]
    assert 'exit 2' in prerequisites or 'exit 2' in text[text.index('Assert-Docker'):text.index('Assert-Docker') + 400]
    assert "exit 3" in text, 'pending/ahead схема → код 3'
