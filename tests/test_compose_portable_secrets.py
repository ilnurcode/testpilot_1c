"""Portable secret override is opt-in and mounts one external key only where needed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'compose.yaml'
OVERRIDE = ROOT / 'compose.portable-secrets.yaml.example'
KEY_TARGET = '/run/secrets/testpilot_connection_key'


def test_portable_secret_override_composes_as_read_only_opt_in(tmp_path):
    assert OVERRIDE.is_file()
    base = BASE.read_text(encoding='utf-8')
    assert 'TP_SECRET_PROTECTOR' not in base
    assert 'TP_PORTABLE_SECRET_KEY_FILE' not in base

    if not shutil.which('docker'):
        pytest.skip('Docker Compose is unavailable in this environment')
    key = tmp_path / 'external-portable.key'
    key.write_text('test-only-key-file\n', encoding='ascii')
    env_file = tmp_path / 'empty.env'
    env_file.write_text('', encoding='utf-8')
    env = os.environ | {'TP_PORTABLE_SECRET_HOST_FILE': str(key)}
    result = subprocess.run(
        ['docker', 'compose', '--env-file', str(env_file), '-f', str(BASE), '-f', str(OVERRIDE),
         '--profile', 'init', 'config', '--format', 'json'],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    config = json.loads(result.stdout)
    for name in ('impact', 'intelligence'):
        service = config['services'][name]
        assert service['environment']['TP_SECRET_PROTECTOR'] == 'portable'
        assert service['environment']['TP_PORTABLE_SECRET_KEY_FILE'] == KEY_TARGET
        mounts = [item for item in service['secrets'] if item['source'] == 'testpilot_connection_key']
        assert mounts == [{'source': 'testpilot_connection_key', 'target': 'testpilot_connection_key', 'mode': '0400'}]
    for name in ('web', 'migrate'):
        assert not any(item['source'] == 'testpilot_connection_key' for item in config['services'][name].get('secrets', []))
    assert config['secrets']['testpilot_connection_key']['file'] == str(key)
