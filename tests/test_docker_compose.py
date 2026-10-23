from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_self_contained_and_host_ports_are_loopback_only():
    compose = (ROOT / 'compose.yaml').read_text()
    assert 'env_file:' not in compose
    assert 'TestPilotLocal123!' in compose
    assert 'TestPilot_Db_Local_Only_2026' in compose
    assert 'WEB_URL: http://web:8000' in compose
    assert '127.0.0.1:8000:8000' in compose
    assert '127.0.0.1:8001:8001' in compose
    assert '127.0.0.1:8002:8002' in compose
    assert 'pgdata:/var/lib/postgresql/data' in compose


def test_compose_configuration_renders_without_env_file_or_shell_setup(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose is unavailable in this environment')
    env_file = tmp_path / 'empty.env'
    env_file.write_text('', encoding='utf-8')
    result = subprocess.run(
        ['docker', 'compose', '--env-file', str(env_file), 'config', '--quiet'],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
