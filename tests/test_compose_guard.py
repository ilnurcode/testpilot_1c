"""Предохранитель scripts/compose.sh: down -v и удаление томов рабочего проекта запрещены.

Docker не вызывается: в PATH подставляется фейковый `docker`, который лишь записывает аргументы.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / 'scripts' / 'compose.sh'


def _bash_exe() -> str:
    r"""bash из Git for Windows: системный C:\Windows\System32\bash.exe — это WSL и не видит /c/..."""
    if os.name != 'nt':
        return 'bash'
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    for candidate in (
        os.path.join(program_files, 'Git', 'bin', 'bash.exe'),
        os.path.join(program_files, 'Git', 'usr', 'bin', 'bash.exe'),
    ):
        if os.path.isfile(candidate):
            return candidate
    return 'bash'


def _posix(path: Path | str) -> str:
    r"""Путь в форме, понятной bash: на Windows C:\x\y превращается в /c/x/y."""
    text = str(path).replace('\\', '/')
    if os.name == 'nt' and len(text) > 1 and text[1] == ':':
        return '/' + text[0].lower() + text[2:]
    return text


@pytest.fixture
def fake_docker(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    args_file = tmp_path / 'args.txt'
    fake = bin_dir / 'docker'
    fake.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "' + _posix(args_file) + '"\nexit 0\n',
        encoding='utf-8',
    )
    fake.chmod(0o755)
    return bin_dir, args_file


def _run(args, fake_docker, ci=None):
    bin_dir, args_file = fake_docker
    env = {**os.environ, 'PATH': f'{_posix(bin_dir)}:/usr/bin:/bin'}
    env.pop('CI', None)
    if ci is not None:
        env['CI'] = ci
    result = subprocess.run([_bash_exe(), _posix(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
    called = args_file.read_text(encoding='utf-8').splitlines() if args_file.exists() else []
    return result, called


def test_down_with_volumes_on_work_project_is_refused(fake_docker):
    result, called = _run(['down', '-v'], fake_docker)

    assert result.returncode == 3
    assert 'Отказано: `down -v` удаляет тома (БД и evidence)' in result.stderr
    assert 'Используйте `-p testpilot-ci` или `-p testpilot-restore`' in result.stderr
    assert called == []


def test_down_with_volumes_is_allowed_for_isolated_project(fake_docker):
    result, called = _run(['-p', 'testpilot-ci', 'down', '-v'], fake_docker)

    assert result.returncode == 0, result.stderr
    assert called == ['compose', '-p', 'testpilot-ci', 'down', '-v']


def test_down_without_volumes_is_allowed(fake_docker):
    result, called = _run(['down'], fake_docker)

    assert result.returncode == 0
    assert called == ['compose', 'down']


def test_up_is_allowed_untouched(fake_docker):
    result, called = _run(['up', '-d', '--wait'], fake_docker)

    assert result.returncode == 0
    assert called == ['compose', 'up', '-d', '--wait']


def test_volume_rm_of_project_volume_is_refused(fake_docker):
    result, called = _run(['volume', 'rm', 'testpilot_pgdata'], fake_docker)

    assert result.returncode == 3
    assert 'удаление томов проекта testpilot' in result.stderr
    assert called == []


def test_volume_prune_is_refused(fake_docker):
    result, called = _run(['volume', 'prune', '-f'], fake_docker)

    assert result.returncode == 3
    assert called == []


def test_down_with_volumes_allowed_in_ci(fake_docker):
    result, called = _run(['down', '-v'], fake_docker, ci='1')

    assert result.returncode == 0, result.stderr
    assert called == ['compose', 'down', '-v']


def test_long_volumes_flag_is_allowed_for_restore_project(fake_docker):
    result, called = _run(['--project', 'testpilot-restore', 'down', '--volumes'], fake_docker)

    assert result.returncode == 0
    assert called == ['compose', '--project', 'testpilot-restore', 'down', '--volumes']
