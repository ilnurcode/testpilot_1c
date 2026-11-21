"""Протекторы секретов: DPAPI Windows и portable AES-GCM без plaintext fallback."""
from __future__ import annotations

import importlib.util

import os
from pathlib import Path

import pytest

from testpilot import secrets


PROJECT_A = '00000000-0000-0000-0000-000000000001'
PROJECT_B = '00000000-0000-0000-0000-000000000002'
ROOT = Path(__file__).resolve().parents[1]


def _secrets_key_script():
    spec = importlib.util.spec_from_file_location('secrets_key', ROOT / 'scripts' / 'secrets_key.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module




def _portable_key(monkeypatch, tmp_path, name='portable.key') -> Path:
    path = tmp_path / name
    secrets.create_portable_key(path)
    monkeypatch.setenv(secrets.PROTECTOR_ENV, 'portable')
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(path))
    return path


def test_windows_dpapi_roundtrip_and_ciphertext_differs(monkeypatch):
    if os.name != 'nt':
        pytest.skip('DPAPI доступен только на Windows')
    monkeypatch.delenv(secrets.PROTECTOR_ENV, raising=False)
    envelope, scheme = secrets.seal('Пароль-подключения-1')
    assert scheme == secrets.SCHEME_DPAPI
    assert envelope.startswith(scheme + ':')
    assert 'Пароль-подключения-1' not in envelope
    assert secrets.open_sealed(envelope) == 'Пароль-подключения-1'
    second, _ = secrets.seal('Пароль-подключения-1')
    assert second != envelope
    assert secrets.open_sealed(second) == 'Пароль-подключения-1'


def test_portable_roundtrip_uses_aesgcm_and_external_key(monkeypatch, tmp_path):
    _portable_key(monkeypatch, tmp_path)
    context = secrets.project_secret_context(PROJECT_A)
    envelope, scheme = secrets.seal('portable-pass', context=context)
    assert scheme == secrets.SCHEME_PORTABLE_AESGCM
    assert envelope.startswith(scheme + ':') and 'portable-pass' not in envelope
    assert secrets.open_sealed(envelope, context=context) == 'portable-pass'
    second, _ = secrets.seal('portable-pass', context=context)
    assert second != envelope
    assert secrets.status() == {
        'protector': secrets.SCHEME_PORTABLE_AESGCM,
        'available': True,
        'scope': 'external-key-file',
        'portable': True,
    }


def test_portable_rejects_wrong_missing_and_malformed_key(monkeypatch, tmp_path):
    key = _portable_key(monkeypatch, tmp_path)
    context = secrets.project_secret_context(PROJECT_A)
    envelope, _ = secrets.seal('portable-pass', context=context)

    other = tmp_path / 'other.key'
    secrets.create_portable_key(other)
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(other))
    with pytest.raises(secrets.SecretsError) as wrong:
        secrets.open_sealed(envelope, context=context)
    assert 'portable-pass' not in str(wrong.value)

    monkeypatch.delenv(secrets.PORTABLE_KEY_ENV)
    unavailable = secrets.status()
    assert unavailable['available'] is False
    assert 'key material' in unavailable['reason'], 'отказ должен называть причину, а не платформу'
    with pytest.raises(secrets.SecretsError):
        secrets.open_sealed(envelope, context=context)

    malformed = tmp_path / 'malformed.key'
    malformed.write_text('not-a-key\n', encoding='ascii')
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(malformed))
    with pytest.raises(secrets.SecretsError):
        secrets.seal('portable-pass', context=context)
    assert key.exists()


def test_portable_rejects_cross_project_ciphertext_swap(monkeypatch, tmp_path):
    _portable_key(monkeypatch, tmp_path)
    envelope, _ = secrets.seal(
        'portable-pass',
        context=secrets.project_secret_context(PROJECT_A),
    )
    with pytest.raises(secrets.SecretsError):
        secrets.open_sealed(envelope, context=secrets.project_secret_context(PROJECT_B))


def test_portable_key_file_safety_rejects_relative_and_checkout_paths(monkeypatch, tmp_path):
    monkeypatch.setenv(secrets.PROTECTOR_ENV, 'portable')
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, 'relative.key')
    with pytest.raises(secrets.SecretsError):
        secrets.seal('portable-pass', context=secrets.project_secret_context(PROJECT_A))

    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(Path(secrets.__file__).resolve()))
    with pytest.raises(secrets.SecretsError):
        secrets.seal('portable-pass', context=secrets.project_secret_context(PROJECT_A))

    key = tmp_path / 'unsafe.key'
    secrets.create_portable_key(key)
    if os.name != 'nt':
        key.chmod(0o644)
        monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(key))
        with pytest.raises(secrets.SecretsError):
            secrets.seal('portable-pass', context=secrets.project_secret_context(PROJECT_A))


def test_empty_password_is_not_sealed():
    with pytest.raises(secrets.SecretsError):
        secrets.seal('')


def test_unknown_scheme_is_refused_and_not_echoed():
    with pytest.raises(secrets.SecretsError) as error:
        secrets.open_sealed('fernet-v1:AAAA')
    assert 'не поддерживается' in str(error.value)
    assert 'fernet-v1' not in str(error.value) and 'AAAA' not in str(error.value)


def test_corrupted_envelope_fails_closed(monkeypatch, tmp_path):
    _portable_key(monkeypatch, tmp_path)
    with pytest.raises(secrets.SecretsError):
        secrets.open_sealed(
            'portable-aesgcm-v1:AAAA',
            context=secrets.project_secret_context(PROJECT_A),
        )


def test_non_windows_auto_mode_fails_closed_without_external_key(monkeypatch):
    monkeypatch.setattr(os, 'name', 'posix')
    monkeypatch.delenv(secrets.PORTABLE_KEY_ENV, raising=False)
    monkeypatch.delenv(secrets.PROTECTOR_ENV, raising=False)
    assert secrets.available() is False
    with pytest.raises(secrets.SecretsError):
        secrets.seal('secret', context=secrets.project_secret_context(PROJECT_A))


def test_portable_key_creation_requires_explicit_confirmation(tmp_path):
    module = _secrets_key_script()
    key = tmp_path / 'explicit.key'
    assert module.main(['--create-portable-key', '--key-file', str(key)]) == 2
    assert not key.exists()
    assert module.main(['--create-portable-key', '--key-file', str(key), '--confirmed']) == 0
    assert key.exists()
