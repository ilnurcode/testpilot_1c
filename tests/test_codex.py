import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from testpilot.ai import codex
from testpilot.ai.api import router


def test_device_prompt_accepts_official_ansi_output():
    output = (b'\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n'
              b'2. Enter this one-time code (expires in 15 minutes)\n'
              b'   \x1b[94mABCD-EFGH\x1b[0m\n')
    assert codex._device_prompt(output) == ('https://auth.openai.com/codex/device', 'ABCD-EFGH')


@pytest.mark.parametrize('uri', [
    'http://auth.openai.com/codex/device',
    'https://auth.openai.com.attacker.invalid/codex/device',
    'https://auth.openai.com@attacker.invalid/codex/device',
    'https://attacker.invalid@auth.openai.com/codex/device',
    'https://auth.openai.com:443/codex/device',
    'https://auth.openai.com/codex/device?token=secret',
    'https://auth.openai.com/codex/device#secret',
    'https://auth.openai.com/other',
])
def test_device_prompt_rejects_untrusted_uri(uri):
    assert codex._device_prompt(f'{uri}\nABCD-EFGH\n'.encode()) == (None, None)


def test_device_prompt_does_not_treat_tokens_as_codes():
    assert codex._device_prompt(b'https://auth.openai.com/codex/device\naccess_token=ABCD-EFGH\n') == (None, None)


@pytest.mark.parametrize('answer', [
    b'not JSON', b'[]', b'{"proposals":[42]}', b'{"proposals":["wrong"]}',
    b'{"proposals":[{"cases":"wrong"}]}',
    b'{"proposals":[{"cases":["not JSON"]}]}',
    b'{"proposals":[{"cases":["42"]}]}',
    b'{"summary":"x","proposals":[]}',
])
def test_invalid_codex_answers_are_sanitized(answer):
    with pytest.raises(HTTPException) as error:
        codex._suggestion(answer)
    assert error.value.status_code == 502
    assert answer.decode() not in str(error.value.detail)


def test_strict_schema_preserves_arbitrary_case_data():
    schema = codex._output_schema()
    pending = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            assert 'default' not in node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    assert schema['$defs']['Proposal']['properties']['cases']['items']['type'] == 'string'
    case = {'entity': 'Catalog_Номенклатура', 'fields': {'Code': '001', 'nested': {'flag': True}},
            'steps': [{'action': 'fill', 'target': 'Наименование', 'value': 'Товар'}],
            'expected': {'count': 1}, 'custom_metadata': {'boundary': 0}}
    result = codex._suggestion(json.dumps({
        'summary': 'Регрессия', 'selected_test_ids': [], 'warnings': [],
        'proposals': [{'title': 'Карточка товара', 'rationale': 'Изменён справочник',
                       'engine': 'playwright', 'object_ids': ['catalog'],
                       'steps': ['Открыть карточку'], 'expected_result': 'Карточка открыта',
                       'cases': [json.dumps(case)]}],
    }))
    assert result.proposals[0].cases[0].fields == case['fields']
    assert result.proposals[0].cases[0].steps == case['steps']
    assert result.proposals[0].cases[0].expected == case['expected']
    assert result.proposals[0].cases[0].model_extra['custom_metadata'] == {'boundary': 0}


@pytest.mark.parametrize('model', ['--config=unsafe', 'gpt model', 'model\nsecret', '../model', 'x' * 129])
def test_invalid_configured_model_is_rejected_before_spawning(monkeypatch, model):
    monkeypatch.setenv('TP_CODEX_MODEL', model)
    monkeypatch.setattr(codex, '_executable', lambda value: pytest.fail('No executable lookup for an invalid model'))
    with pytest.raises(HTTPException) as error:
        codex._configuration()
    assert error.value.status_code == 503
    assert model not in str(error.value.detail)


def test_windows_npm_shim_resolves_to_native_pinned_layout(monkeypatch, tmp_path):
    shim = tmp_path / 'codex.cmd'
    shim.write_text('@echo off', encoding='utf-8')
    native = tmp_path / 'node_modules' / '@openai' / 'codex-win32-x64' / 'vendor' / 'x86_64-pc-windows-msvc' / 'bin' / 'codex.exe'
    native.parent.mkdir(parents=True)
    native.write_bytes(b'native-file-for-discovery-only')
    monkeypatch.setattr(codex.shutil, 'which', lambda value: str(shim))
    monkeypatch.setattr(codex.platform, 'machine', lambda: 'AMD64')
    assert codex._executable('codex') == str(native.resolve())
    native.unlink()
    assert codex._executable('codex') is None


def test_host_codex_configuration_is_not_used(monkeypatch, tmp_path):
    monkeypatch.setenv('TP_CODEX_MODEL', '')
    monkeypatch.setenv('TP_CODEX_TIMEOUT_SECONDS', '180')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'config.toml').write_text('model = "private-model"', encoding='utf-8')
    with pytest.raises(HTTPException) as error:
        codex._configuration()
    assert error.value.status_code == 503
    assert (tmp_path / 'config.toml').read_text(encoding='utf-8') == 'model = "private-model"'


def test_child_environment_has_no_host_auth_or_project_secrets(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'TP_LLM_API_KEY', 'TP_EXPERT_PASSWORD',
                'DATABASE_URL', 'NODE_OPTIONS', 'OPENAI_BASE_URL', 'CODEX_ISSUER_BASE_URL'):
        monkeypatch.setenv(key, 'do-not-inherit')
    cwd = tmp_path / 'work'
    cwd.mkdir()
    home = tmp_path / 'credentials'
    env = codex._environment(home, cwd)
    assert 'do-not-inherit' not in env.values()
    assert not any(key.startswith('TP_') for key in env)
    assert env['CODEX_HOME'] == str(home)
    assert env['HOME'] == str(cwd / 'home')
    assert env['USERPROFILE'] == str(cwd / 'home')
    assert Path(env['XDG_CONFIG_HOME']).is_relative_to(cwd)


def test_status_never_returns_cli_diagnostics(monkeypatch, tmp_path):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, None, 1)
    monkeypatch.setattr(codex, '_configuration', lambda: config)
    monkeypatch.setattr(client, '_available', lambda config: True)
    monkeypatch.setattr(client, '_command', lambda *args, **kwargs: (1, b'Error with access_token=do-not-return'))
    result = client.status()
    assert result['state'] == 'error'
    assert not result['authenticated']
    assert set(result) == {'available', 'authenticated', 'state', 'model', 'verification_uri', 'user_code', 'message'}
    assert 'do-not-return' not in json.dumps(result)
    assert result['verification_uri'] is None and result['user_code'] is None


def test_api_key_status_cannot_authorize_chatgpt_generation(monkeypatch, tmp_path):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, None, 1)
    monkeypatch.setattr(codex, '_configuration', lambda: config)
    monkeypatch.setattr(client, '_available', lambda config: True)
    monkeypatch.setattr(client, '_command', lambda *args, **kwargs: (0, b'API key configured - secret'))
    result = client.status()
    assert result['state'] == 'signed_out'
    assert not result['authenticated']
    assert 'secret' not in json.dumps(result)


def test_login_returns_pending_without_waiting_for_user(monkeypatch, tmp_path):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, None, 1)
    monkeypatch.setattr(codex, '_configuration', lambda: config)
    monkeypatch.setattr(client, '_available', lambda config: True)
    started = threading.Event()
    cancelled = threading.Event()

    def command(config, arguments, **kwargs):
        if arguments == ['login', 'status']:
            return 1, b'Not logged in'
        assert arguments == ['login', '--device-auth']
        started.set()
        assert kwargs['cancel'].wait(5), 'Device flow must be cancelled on shutdown'
        cancelled.set()
        raise codex._CommandError('cancelled')

    monkeypatch.setattr(client, '_command', command)
    try:
        result = client.login()
        assert result['state'] == 'pending'
        assert started.wait(1)
        assert client._login_thread.is_alive()
        assert client.login()['state'] == 'pending'
    finally:
        client.shutdown()
    assert cancelled.is_set()
    assert client._login_thread is None
    assert client._uri is None and client._code is None


def test_codex_login_and_logout_are_expert_only(monkeypatch):
    monkeypatch.setenv('TP_ANALYST_PASSWORD', 'unit-password')
    monkeypatch.setenv('TP_RUNNER_PASSWORD', 'unit-password')
    monkeypatch.setattr(codex, 'login', lambda: pytest.fail('Unauthorized login must not start a process'))
    monkeypatch.setattr(codex, 'logout', lambda: pytest.fail('Unauthorized logout must not touch credentials'))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for endpoint in ('login', 'logout'):
            assert client.post('/api/v1/ai/codex/' + endpoint).status_code == 401
            for role in ('analyst', 'runner'):
                assert client.post('/api/v1/ai/codex/' + endpoint, auth=(role, 'unit-password')).status_code == 403


@pytest.mark.parametrize("role", ["analyst", "runner", "expert"])
@pytest.mark.parametrize("endpoint", ["/api/v1/ai/codex/status", "/api/v1/ai/status"])
def test_device_code_is_visible_only_to_expert(monkeypatch, role, endpoint):
    monkeypatch.setenv("TP_" + role.upper() + "_PASSWORD", "unit-password")
    pending = {"available": True, "authenticated": False, "state": "pending",
               "model": None, "verification_uri": "https://auth.openai.com/codex/device",
               "user_code": "ABCD-EFGH", "message": "Ожидается вход"}
    monkeypatch.setattr(codex, "status", lambda: dict(pending))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(endpoint, auth=(role, "unit-password"))
    assert response.status_code == 200
    result = response.json()
    if endpoint == "/api/v1/ai/status":
        result = result["codex"]
    assert result["state"] == "pending"
    assert result["user_code"] == ("ABCD-EFGH" if role == "expert" else None)
    assert result["verification_uri"] == (pending["verification_uri"] if role == "expert" else None)


def test_oversized_context_is_rejected_before_login(monkeypatch):
    client = codex.CodexClient()
    monkeypatch.setattr(codex, '_configuration', lambda: pytest.fail('Oversized context must not start Codex'))
    package = {'configuration_id': 'unit', 'target_version': '2', 'changed_objects': ['a'],
               'affected_objects': ['a'], 'dependency_paths': [], 'synthetic': False, 'warnings': []}
    with pytest.raises(HTTPException) as error:
        client.generate(package, [], {}, [{'text': 'я' * 60000}])
    assert error.value.status_code == 422


@pytest.mark.parametrize(('diagnostic', 'status', 'message'), [
    (b'Error: Failed to refresh token: status=401 Unauthorized', 503,
     'Codex не смог обновить вход ChatGPT. Обратитесь к эксперту: на странице «Нейросеть и проверки» отключите ChatGPT и войдите заново.'),
    (b'Error: request failed: status=401 Unauthorized', 502,
     'Codex отклонил запрос. Проверьте вход ChatGPT, доступность выбранной модели и лимиты аккаунта.'),
    (b'Error: Failed to refresh token: status=403 Forbidden', 502,
     'Codex отклонил запрос. Проверьте вход ChatGPT, доступность выбранной модели и лимиты аккаунта.'),
    (b'Failed to refresh token: status=403\nOther request: status=401 Unauthorized', 502,
     'Codex отклонил запрос. Проверьте вход ChatGPT, доступность выбранной модели и лимиты аккаунта.'),
])
def test_generation_sanitizes_subprocess_auth_errors(monkeypatch, tmp_path, diagnostic, status, message):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, None, 5)
    monkeypatch.setattr(codex, '_configuration', lambda: config)
    monkeypatch.setattr(client, '_read_status', lambda config: {'authenticated': True})
    monkeypatch.setattr(codex.provider, 'generation_messages', lambda *args, **kwargs: ('system', 'content'))
    run = client._run
    secret = b'access_token=never-expose-this-secret'
    script = ('import sys; sys.stdin.buffer.read(); '
              f'sys.stderr.buffer.write({(diagnostic + b" " + secret)!r}); sys.exit(1)')

    def run_fake_codex(argv, **kwargs):
        return run([sys.executable, '-I', '-c', script], **kwargs)

    monkeypatch.setattr(client, '_run', run_fake_codex)
    with pytest.raises(HTTPException) as error:
        client.generate({}, [], {})
    assert error.value.status_code == status
    assert error.value.detail == message
    assert secret.decode() not in str(error.value.detail)
    assert diagnostic.decode() not in str(error.value.detail)


def _running(pid):
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        return state != 'Z'
    except FileNotFoundError:
        return False


def _stop_owned_processes(pids):
    for pid in pids:
        if _running(pid):
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            else:
                os.kill(pid, 9)


@pytest.mark.skipif(os.name != 'nt' and sys.platform != 'linux', reason='Process state probe is Linux/Windows-specific')
@pytest.mark.parametrize('parent_waits', [True, False])
def test_process_group_cleanup_after_timeout_or_parent_exit(tmp_path, parent_waits):
    client = codex.CodexClient()
    script = ("import json,os,pathlib,subprocess,sys,time; "
              "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
              "pathlib.Path('pids.json').write_text(json.dumps([os.getpid(),child.pid])); "
              + ('time.sleep(60)' if parent_waits else 'sys.exit(0)'))
    pids = []
    try:
        if parent_waits:
            with pytest.raises(codex._CommandError, match='^timeout$'):
                client._run([sys.executable, '-I', '-c', script], cwd=tmp_path,
                            env=codex._environment(tmp_path / 'credentials', tmp_path), timeout=2)
        else:
            code, _ = client._run([sys.executable, '-I', '-c', script], cwd=tmp_path,
                                  env=codex._environment(tmp_path / 'credentials', tmp_path), timeout=5)
            assert code == 0
        pids = json.loads((tmp_path / 'pids.json').read_text())
        assert not any(_running(pid) for pid in pids)
        assert not client._processes
    finally:
        if not pids and (tmp_path / 'pids.json').exists():
            pids = json.loads((tmp_path / 'pids.json').read_text())
        _stop_owned_processes(pids)
        client.shutdown()


def test_output_limit_stops_real_subprocess(tmp_path):
    client = codex.CodexClient()
    with pytest.raises(codex._CommandError, match='^output$'):
        client._run([sys.executable, '-I', '-c', 'import sys,time;sys.stdout.write("x"*4096);sys.stdout.flush();time.sleep(60)'],
                    cwd=tmp_path, env=codex._environment(tmp_path / 'credentials', tmp_path), timeout=5, limit=1024)
    assert not client._processes


def test_timeout_interrupts_blocked_stdin_writer(tmp_path):
    client = codex.CodexClient()
    with pytest.raises(codex._CommandError, match='^timeout$'):
        client._run([sys.executable, '-I', '-c', 'import time; time.sleep(60)'],
                    cwd=tmp_path, env=codex._environment(tmp_path / 'credentials', tmp_path),
                    timeout=0.2, input_data=b'x' * 100000)
    assert not client._processes


def test_successful_login_clears_previous_error(monkeypatch, tmp_path):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, None, 1)
    monkeypatch.setattr(codex, "_configuration", lambda: config)
    monkeypatch.setattr(client, "_available", lambda config: True)
    replies = iter([(0, b"Logged in using ChatGPT"), (1, b"Not logged in")])
    monkeypatch.setattr(client, "_command", lambda *args, **kwargs: next(replies))
    client._state, client._message = "error", "Предыдущая ошибка входа"
    assert client.status()["state"] == "authenticated"
    assert client.status()["state"] == "signed_out"


@pytest.mark.parametrize("failure", ["cancel", "logout"])
def test_logout_failure_does_not_hide_available_cli(monkeypatch, tmp_path, failure):
    client = codex.CodexClient()
    config = codex._Configuration(sys.executable, tmp_path, "unit-model", 1)
    monkeypatch.setattr(codex, "_configuration", lambda: config)
    monkeypatch.setattr(client, "_available", lambda config: True)
    monkeypatch.setattr(client, "_cancel", lambda: failure != "cancel")
    def command(*args, **kwargs):
        raise codex._CommandError("timeout")
    monkeypatch.setattr(client, "_command", command)
    result = client.logout()
    assert result["state"] == "error"
    assert result["available"] is True
    assert result["model"] == "unit-model"


def test_cancel_waits_for_generation_before_process_registration():
    client = codex.CodexClient()
    cancel, finished = threading.Event(), threading.Event()
    client._generations.add(cancel)
    result = []
    def stop():
        result.append(client._cancel())
        finished.set()
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        assert cancel.wait(1)
        assert not finished.wait(0.1)
    finally:
        with client._idle:
            client._generations.discard(cancel)
            client._idle.notify_all()
        thread.join(2)
    assert finished.is_set()
    assert result == [True]
