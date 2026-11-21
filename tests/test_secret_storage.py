"""Хранение пароля подключения: шифрование, upgrade 0009→0010, legacy-миграция.

Тесты идут только на одноразовой схеме A1-харнесса. Открытый legacy-текст не отдаётся
для использования: он остаётся в БД до явной проверенной миграции.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from testpilot import db, secrets
from testpilot.db import ProjectRow

ROOT = Path(__file__).resolve().parents[1]
SECRET_COLUMNS = ('auth_secret', 'auth_secret_scheme', 'auth_secret_updated_at')
needs_dpapi = pytest.mark.skipif(os.name != 'nt', reason='DPAPI доступен только на Windows; Docker-шифрование BLOCKED')


def _session():
    return db._factory()


def _alembic(direction: str, revision: str):
    """Upgrade/downgrade одноразовой схемы через bound connection (как support_pg)."""
    from alembic import command
    from alembic.config import Config
    config = Config(str(ROOT / 'alembic.ini'))
    with db._engine.begin() as connection:
        config.attributes['connection'] = connection
        getattr(command, direction)(config, revision)


def _columns_present(session) -> bool:
    rows = session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'impact_projects' AND table_schema = current_schema() "
        "AND column_name IN ('auth_secret', 'auth_secret_scheme', 'auth_secret_updated_at')"
    )).scalars().all()
    return len(set(rows)) == len(SECRET_COLUMNS)


def _row(project_id):
    session = _session()
    try:
        if _columns_present(session):
            return session.execute(text(
                'SELECT auth_secret, auth_secret_scheme, auth_secret_updated_at, auth_password '
                'FROM impact_projects WHERE id = :id'
            ), {'id': project_id}).one()
        legacy = session.execute(text('SELECT auth_password FROM impact_projects WHERE id = :id'),
                                 {'id': project_id}).scalar()
        return (None, None, None, legacy)
    finally:
        session.close()


def _create_project(client, name='Проект A4'):
    response = client.post('/api/v1/projects', json={'name': name, 'description': ''})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _set_password(client, project, password, monkeypatch):
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    return client.put(f'/api/v1/projects/{project}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'auth_username': 'Администратор',
        'auth_password': password,
    })



def _portable_key(monkeypatch, tmp_path):
    path = tmp_path / 'portable.key'
    secrets.create_portable_key(path)
    monkeypatch.setenv(secrets.PROTECTOR_ENV, 'portable')
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(path))
    return path

def _secrets_key():
    spec = importlib.util.spec_from_file_location('secrets_key', ROOT / 'scripts' / 'secrets_key.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_dpapi
def test_encrypted_storage_roundtrip_and_no_plaintext(clients, monkeypatch):
    from testpilot.impact.projects import load_connection_password

    impact, _ = clients
    project = _create_project(impact)

    response = _set_password(impact, project, 'secret-pass', monkeypatch)
    assert response.status_code == 200, response.text
    settings = response.json()['connection_settings']
    assert settings['has_password'] is True and settings['password_encrypted'] is True

    secret, scheme, updated_at, legacy = _row(project)
    assert legacy is None, 'legacy-поле не заполняется'
    assert scheme == secrets.SCHEME_DPAPI and updated_at
    assert secret.startswith(secrets.SCHEME_DPAPI + ':') and 'secret-pass' not in secret
    assert secrets.open_sealed(secret) == 'secret-pass'

    session = _session()
    try:
        loaded, warning = load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()
    assert loaded == 'secret-pass' and warning is None

    # Ротация пароля: старый шифротекст заменяется только новой записью, обе расшифровываются своим ключом.
    assert _set_password(impact, project, 'secret-pass-2', monkeypatch).status_code == 200
    second, _, _, legacy2 = _row(project)
    assert legacy2 is None and second != secret
    assert secrets.open_sealed(second) == 'secret-pass-2'





def test_portable_storage_roundtrip_and_context_bound_row(clients, monkeypatch, tmp_path):
    from testpilot.impact.projects import load_connection_password

    _portable_key(monkeypatch, tmp_path)
    impact, _ = clients
    project = _create_project(impact, 'Проект portable A4')
    response = _set_password(impact, project, 'portable-pass', monkeypatch)
    assert response.status_code == 200, response.text

    secret, scheme, _, legacy = _row(project)
    assert legacy is None and scheme == secrets.SCHEME_PORTABLE_AESGCM
    assert 'portable-pass' not in secret
    context = secrets.project_secret_context(project)
    assert secrets.open_sealed(secret, context=context) == 'portable-pass'

    session = _session()
    try:
        loaded, warning = load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()
    assert loaded == 'portable-pass' and warning is None


def test_missing_portable_key_returns_503_without_mutating_row(clients, monkeypatch, tmp_path):
    _portable_key(monkeypatch, tmp_path)
    impact, _ = clients
    project = _create_project(impact, 'Проект portable key loss')
    assert _set_password(impact, project, 'portable-pass', monkeypatch).status_code == 200
    before = _row(project)

    monkeypatch.delenv(secrets.PORTABLE_KEY_ENV)
    response = _set_password(impact, project, 'new-portable-pass', monkeypatch)
    assert response.status_code == 503, response.text
    assert 'new-portable-pass' not in response.text
    assert _row(project) == before

    # Даже явное подтверждение не помогает без ключа: seal() отказывает, ciphertext прежний.
    forced = impact.put(f'/api/v1/projects/{project}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'auth_username': 'Администратор',
        'auth_password': 'new-portable-pass', 'rekey_confirm': True,
    })
    assert forced.status_code == 503, forced.text
    assert 'new-portable-pass' not in forced.text
    assert _row(project) == before


def test_portable_rekey_without_confirmation_is_refused_and_preserves_ciphertext(clients, monkeypatch, tmp_path):
    from testpilot.impact.projects import load_connection_password

    _portable_key(monkeypatch, tmp_path)
    impact, _ = clients
    project = _create_project(impact, 'Проект rekey без подтверждения')
    assert _set_password(impact, project, 'portable-pass', monkeypatch).status_code == 200
    before = _row(project)

    other = tmp_path / 'other.key'
    secrets.create_portable_key(other)
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(other))

    session = _session()
    try:
        with pytest.raises(secrets.SecretsError):
            load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()

    refused = _set_password(impact, project, 'rekey-pass', monkeypatch)
    assert refused.status_code == 409, refused.text
    assert 'rekey_confirm' in refused.text and 'rekey-pass' not in refused.text
    assert _row(project) == before, 'отказ не меняет сохранённый ciphertext байт-в-байт'


def test_portable_rekey_with_confirmation_replaces_secret(clients, monkeypatch, tmp_path):
    from testpilot.impact.projects import load_connection_password

    _portable_key(monkeypatch, tmp_path)
    impact, _ = clients
    project = _create_project(impact, 'Проект осознанный rekey')
    assert _set_password(impact, project, 'portable-pass', monkeypatch).status_code == 200
    before = _row(project)

    other = tmp_path / 'other.key'
    secrets.create_portable_key(other)
    monkeypatch.setenv(secrets.PORTABLE_KEY_ENV, str(other))

    confirmed = impact.put(f'/api/v1/projects/{project}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'auth_username': 'Администратор',
        'auth_password': 'rekey-pass', 'rekey_confirm': True,
    })
    assert confirmed.status_code == 200, confirmed.text

    after = _row(project)
    assert after[0] != before[0] and after[1] == secrets.SCHEME_PORTABLE_AESGCM
    context = secrets.project_secret_context(project)
    assert secrets.open_sealed(after[0], context=context) == 'rekey-pass'

    session = _session()
    try:
        loaded, warning = load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()
    assert loaded == 'rekey-pass' and warning is None

def test_upgrade_0009_to_0010_creates_columns_and_no_drift(clients, monkeypatch):
    impact, _ = clients
    project = _create_project(impact, 'Проект upgrade')

    _alembic('downgrade', '0009')
    session = _session()
    try:
        assert _columns_present(session) is False
    finally:
        session.close()
    # Схема отстала от кода: и health, и запись соединения отвечают понятным 503.
    assert impact.get('/health').status_code == 503
    refused = _set_password(impact, project, 'secret-pass', monkeypatch)
    assert refused.status_code == 503
    assert 'устарел' in refused.text.lower() or 'миграц' in refused.text.lower()
    assert _row(project)[3] is None, 'открытый текст не сохраняется'

    _alembic('upgrade', 'head')
    session = _session()
    try:
        assert _columns_present(session) is True
        with db._engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            diff = compare_metadata(context, db.Base.metadata)
        mine = [item for item in diff if 'impact_projects' in repr(item)]
        assert mine == [], f'ORM и миграция 0010 расходятся по impact_projects: {mine}'
    finally:
        session.close()
    assert _set_password(impact, project, 'secret-pass', monkeypatch).status_code == 200


@needs_dpapi
def test_legacy_plaintext_requires_explicit_verified_migration(clients, monkeypatch):
    from testpilot.impact.projects import load_connection_password

    impact, _ = clients
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    project = _create_project(impact, 'Проект с legacy')
    assert impact.put(f'/api/v1/projects/{project}/connection',
                      json={'odata_url': 'http://127.0.0.1:8001/odata',
                            'auth_username': 'Администратор'}).status_code == 200
    session = _session()
    try:
        session.execute(text('UPDATE impact_projects SET auth_password = :p WHERE id = :id'),
                        {'p': 'legacy-pass', 'id': project})
        session.commit()
    finally:
        session.close()

    session = _session()
    try:
        with pytest.raises(secrets.SecretsError) as error:
            load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()
    message = str(error.value)
    assert 'migrate-legacy' in message and 'legacy-pass' not in message, 'открытый текст не должен эхоиться'

    # Диагностика с legacy-паролем не выполняется и возвращает понятный отказ.
    blocked = impact.post(f'/api/v1/projects/{project}/connection/probe')
    assert blocked.status_code == 503

    module = _secrets_key()
    assert module.main(['--migrate-legacy']) == 2
    assert _row(project)[3] == 'legacy-pass', 'без подтверждения открытый текст не удаляется'

    assert module.main(['--migrate-legacy', '--confirmed']) == 0
    secret, scheme, _, legacy = _row(project)
    assert legacy is None and scheme == secrets.SCHEME_DPAPI
    assert secrets.open_sealed(secret) == 'legacy-pass'
    assert module.main(['--verify']) == 0


@needs_dpapi
def test_wrong_protector_is_refused_and_does_not_mutate_rows(clients, monkeypatch):
    from testpilot.impact.projects import load_connection_password

    impact, _ = clients
    project = _create_project(impact, 'Проект чужой схемы')
    assert _set_password(impact, project, 'secret-pass', monkeypatch).status_code == 200
    session = _session()
    try:
        # Схема авторитетна в envelope: имитируем секрет, сохранённый другим профилем.
        session.execute(text("UPDATE impact_projects SET auth_secret = 'fernet-v1:AAAA', "
                             "auth_secret_scheme = 'fernet-v1' WHERE id = :id"), {'id': project})
        session.commit()
        before = _row(project)
    finally:
        session.close()

    session = _session()
    try:
        with pytest.raises(secrets.SecretsError) as error:
            load_connection_password(session, session.get(ProjectRow, project))
    finally:
        session.close()
    assert 'не поддерживается' in str(error.value)
    assert 'secret-pass' not in str(error.value)
    assert _row(project) == before, 'отказ не должен менять сохранённый шифротекст'

    assert _secrets_key().main(['--verify']) == 2, 'чужая схема видна как ошибка проверки'


def test_no_secret_material_in_project_api_response(clients):
    impact, _ = clients
    project = _create_project(impact, 'Проект без секретов')
    body = impact.get(f'/api/v1/projects/{project}').text
    assert 'auth_password' not in body and 'auth_secret' not in body


def test_connection_identity_is_stable_and_has_no_secret(clients, monkeypatch):
    from testpilot.db import ProjectRow
    from testpilot.impact.projects import canonical_principal, connection_public_identity, target_identity

    impact, _ = clients
    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    project = _create_project(impact, 'Проект идентичности')
    assert impact.put(f'/api/v1/projects/{project}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'web_url': 'http://127.0.0.1:8000/web',
        'auth_username': '  Администратор ',
    }).status_code == 200

    session = _session()
    try:
        row = session.get(ProjectRow, project)
        public = connection_public_identity(session, row)
        first = target_identity(session, row)
        second = target_identity(session, row)
    finally:
        session.close()

    assert set(public) == {'odata_url', 'web_url', 'auth_username', 'has_password', 'connection_revision'}
    assert public['auth_username'] == canonical_principal('  Администратор ') == 'Администратор'
    assert public['has_password'] is False and len(public['connection_revision']) == 16
    assert 'auth_password' not in str(public) and 'secret' not in str(public).lower()
    assert first == second and project not in first

    other = _create_project(impact, 'Проект-близнец')
    assert impact.put(f'/api/v1/projects/{other}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'web_url': 'http://127.0.0.1:8000/web',
        'auth_username': 'Администратор',
    }).status_code == 200
    session = _session()
    try:
        assert target_identity(session, session.get(ProjectRow, other)) == first
    finally:
        session.close()

    assert impact.put(f'/api/v1/projects/{project}/connection', json={
        'odata_url': 'http://127.0.0.1:8001/odata', 'web_url': 'http://127.0.0.1:8000/web',
        'auth_username': 'ДругойПользователь',
    }).status_code == 200
    session = _session()
    try:
        assert target_identity(session, session.get(ProjectRow, project)) != first
    finally:
        session.close()


def test_denied_target_is_rejected_before_any_request(clients, monkeypatch):
    import testpilot.impact.projects as projects_module

    impact, _ = clients
    calls: list[object] = []
    monkeypatch.setattr(projects_module, 'probe', lambda profile: calls.append(profile) or {'odata': {}, 'web': {}})
    project = _create_project(impact, 'Проект политики')

    denied = impact.put(f'/api/v1/projects/{project}/connection',
                        json={'odata_url': 'https://evil.example.test/odata'})
    assert denied.status_code == 422
    assert 'не входит в TP_TARGET_ALLOWLIST' in denied.text
    assert calls == [], 'запрос не должен отправляться до проверки политики'

    session = _session()
    try:
        session.execute(text("UPDATE impact_projects SET odata_url = 'https://evil.example.test/odata' WHERE id = :id"),
                        {'id': project})
        session.commit()
    finally:
        session.close()
    assert impact.post(f'/api/v1/projects/{project}/connection/probe').status_code == 422
    assert calls == []

    monkeypatch.setenv('TP_ALLOW_LOCAL_TARGETS', '1')
    assert impact.put(f'/api/v1/projects/{project}/connection',
                      json={'odata_url': 'http://127.0.0.1:8001/odata'}).status_code == 200
    assert impact.post(f'/api/v1/projects/{project}/connection/probe').status_code == 200
    assert len(calls) == 1
