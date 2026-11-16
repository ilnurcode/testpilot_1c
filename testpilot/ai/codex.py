"""Изолированный транспорт официального Codex CLI с отдельным входом стенда."""
from contextlib import suppress
import ctypes
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import ValidationError

from testpilot.ai import provider
from testpilot.ai.models import Case, CodexStatus, Suggestion
from testpilot.contracts import canonical

CLI_VERSION = '0.167.2'
MAX_OUTPUT = 512 * 1024
MAX_AUTH_OUTPUT = 64 * 1024
AUTH_TIMEOUT = 10
LOGIN_TIMEOUT = 15 * 60
_ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
_CODE = re.compile(r'(?m)^\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*$')


class _CommandError(Exception):
    """Из исполнителя выходит только ограниченная причина ошибки без секретов."""


@dataclass(frozen=True)
class _Configuration:
    executable: str
    home: Path
    model: str | None
    timeout: int


def _executable(value):
    found = shutil.which(value)
    if not found:
        return None
    path = Path(found).resolve()
    if path.suffix.lower() not in ('.cmd', '.bat'):
        return str(path)
    # npm-обёртки Windows требуют cmd.exe. Используем официальный native binary,
    # не передавая настраиваемую обёртку неявной командной оболочке.
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    target = 'aarch64-pc-windows-msvc' if arch == 'arm64' else 'x86_64-pc-windows-msvc'
    modules = path.parent / 'node_modules'
    package = modules / '@openai' / 'codex'
    vendor = Path('vendor') / target / 'bin' / 'codex.exe'
    for candidate in (
        modules / '@openai' / f'codex-win32-{arch}' / vendor,
        package / 'node_modules' / '@openai' / f'codex-win32-{arch}' / vendor,
        package / vendor,
    ):
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _configuration():
    model = os.environ.get('TP_CODEX_MODEL', '').strip() or None
    if model is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', model):
        raise HTTPException(503, 'Некорректный TP_CODEX_MODEL. Укажите идентификатор модели Codex без пробелов и параметров CLI.')
    try:
        timeout = int(os.environ.get('TP_CODEX_TIMEOUT_SECONDS', '180'))
        if not 1 <= timeout <= 900:
            raise ValueError
    except ValueError:
        raise HTTPException(503, 'TP_CODEX_TIMEOUT_SECONDS должен быть целым числом от 1 до 900.') from None
    default_root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) if os.name == 'nt' else Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    home = Path(os.environ.get('CODEX_HOME', str(default_root / 'testpilot' / 'codex'))).expanduser().resolve()
    if home == (Path.home() / '.codex').resolve():
        raise HTTPException(503, 'Для стенда нужен отдельный CODEX_HOME, не домашний каталог настроек Codex.')
    if any((home / name).exists() for name in ('config.toml', 'AGENTS.md', 'AGENTS.override.md', 'skills', '.agents')):
        raise HTTPException(503, 'Используйте отдельный CODEX_HOME стенда без пользовательских config.toml, AGENTS и skills.')
    executable = _executable(os.environ.get('TP_CODEX_BIN', 'codex').strip())
    return _Configuration(executable or '', home, model, timeout)


def _environment(home, cwd):
    # Разрешённый набор исключает пароли БД, TP_*, API-ключи, NODE_OPTIONS,
    # сторонние сервисы входа и настройки Codex/OMP пользователя.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL',
               'LC_CTYPE', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'HTTPS_PROXY',
               'HTTP_PROXY', 'NO_PROXY', 'ALL_PROXY'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    isolated = cwd / 'home'
    isolated.mkdir(mode=0o700)
    for name in ('config', 'data', 'cache', 'appdata', 'localappdata', 'tmp'):
        (isolated / name).mkdir(mode=0o700)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    env.update(CODEX_HOME=str(home), HOME=str(isolated), USERPROFILE=str(isolated),
               XDG_CONFIG_HOME=str(isolated / 'config'), XDG_DATA_HOME=str(isolated / 'data'),
               XDG_CACHE_HOME=str(isolated / 'cache'), APPDATA=str(isolated / 'appdata'),
               LOCALAPPDATA=str(isolated / 'localappdata'), TMP=str(isolated / 'tmp'),
               TEMP=str(isolated / 'tmp'), NO_COLOR='1', TERM='dumb')
    return env


def _auth_arguments():
    return ['-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"',
            '-c', 'features.hooks=false', '-c', 'features.plugins=false',
            '-c', 'skills.bundled.enabled=false', '-c', 'skills.include_instructions=false',
            '-c', 'mcp_servers={}', '-c', 'project_doc_max_bytes=0']


def _device_prompt(output):
    text = _ANSI.sub('', output.decode('utf-8', errors='replace'))
    uri = None
    for candidate in re.findall(r'https://[^\s<>"\x00-\x1f]+', text):
        try:
            parsed = urlsplit(candidate)
            if (parsed.scheme == 'https' and parsed.netloc == 'auth.openai.com'
                    and parsed.path.rstrip('/') == '/codex/device'
                    and not parsed.query and not parsed.fragment):
                uri = 'https://auth.openai.com/codex/device'
        except ValueError:
            continue
    match = _CODE.search(text)
    # Не выдаём произвольные коды и адреса из диагностических строк.
    return (uri, match.group(1)) if uri and match else (None, None)


class _WindowsJob:
    """KILL_ON_JOB_CLOSE завершает потомков даже после выхода родителя."""
    def __init__(self, process):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise _CommandError('launch')
        info = ExtendedLimits()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))
                or not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle)))):
            self.close()
            raise _CommandError('launch')
        try:
            self._resume(process.pid)
        except _CommandError:
            self.close()
            raise

    def _resume(self, pid):
        # Popen закрывает дескриптор главного потока. Находим приостановленный
        # поток, чтобы потомки не появились до назначения в kill-on-close job.
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                        ('thread_id', wintypes.DWORD), ('process_id', wintypes.DWORD),
                        ('base_priority', wintypes.LONG), ('delta_priority', wintypes.LONG),
                        ('flags', wintypes.DWORD)]

        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32First.restype = wintypes.BOOL
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise _CommandError('launch')
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process_id == pid:
                    thread = kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise _CommandError('launch')
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise _CommandError('launch')
                        return
                    finally:
                        kernel.CloseHandle(thread)
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise _CommandError('launch')
        finally:
            kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill_process(process, job=None):
    if job is not None:
        job.close()
    elif os.name == 'nt':
        # Назначение Windows Job не удалось: не оставляем дочерний процесс.
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], shell=False,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _output_schema():
    schema = Suggestion.model_json_schema()
    # Строгая схема Responses не поддерживает произвольные словари.
    # Передаём каждый Case JSON-строкой и декодируем перед проверкой
    # Suggestion, сохраняя все поля существующего контракта.
    schema['$defs']['Proposal']['properties']['cases']['items'] = {
        'type': 'string',
        'description': 'JSON-объект одного Case, закодированный строкой. Схема Case: ' + canonical(Case.model_json_schema()),
    }
    schema['$defs'].pop('Case', None)

    def close_objects(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for value in node.values():
                close_objects(value)
        elif isinstance(node, list):
            for value in node:
                close_objects(value)

    close_objects(schema)
    return schema


def _suggestion(answer):
    try:
        payload = json.loads(answer)
        if not isinstance(payload, dict) or not isinstance(payload.get('proposals'), list):
            raise ValueError
        for proposal in payload['proposals']:
            if not isinstance(proposal, dict) or not isinstance(proposal.get('cases', []), list):
                raise ValueError
            proposal['cases'] = [json.loads(case) for case in proposal.get('cases', [])]
            if any(not isinstance(case, dict) for case in proposal['cases']):
                raise ValueError
        return Suggestion.model_validate(payload)
    except (ValueError, KeyError, TypeError, RecursionError, ValidationError):
        raise HTTPException(502, 'Ответ Codex не соответствует контракту. Проекты и каталог не изменены.') from None


class CodexClient:
    def __init__(self):
        self._lock = threading.RLock()
        self._auth_lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._processes = {}
        self._generations = set()
        self._login_thread = None
        self._login_cancel = None
        self._state = None
        self._message = ''
        self._uri = self._code = None
        self._version_identity = None

    def _run(self, argv, *, cwd, env, timeout, cancel=None, input_data=None,
             limit=MAX_AUTH_OUTPUT, on_output=None, output_file=None):
        cancel = cancel if cancel is not None else threading.Event()
        if cancel.is_set():
            raise _CommandError('cancelled')
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004} if os.name == 'nt' else {'start_new_session': True}
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, shell=False,
                                       stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
        except OSError:
            raise _CommandError('launch') from None
        job = None
        output = bytearray()
        overflow = threading.Event()
        io_error = threading.Event()
        reader = writer = None
        try:
            with self._idle:
                self._processes[process] = cancel
                if cancel.is_set():
                    raise _CommandError('cancelled')
            if os.name == 'nt':
                job = _WindowsJob(process)

            def read_output():
                try:
                    while chunk := process.stdout.read1(4096):
                        if len(output) + len(chunk) > limit:
                            overflow.set()
                            return
                        output.extend(chunk)
                        if on_output:
                            on_output(bytes(output))
                except OSError:
                    io_error.set()

            def write_input():
                try:
                    process.stdin.write(input_data)
                    process.stdin.close()
                except (OSError, ValueError):
                    io_error.set()

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            if input_data is not None:
                writer = threading.Thread(target=write_input, daemon=True)
                writer.start()
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel.is_set():
                    raise _CommandError('cancelled')
                if overflow.is_set():
                    raise _CommandError('output')
                if io_error.is_set():
                    raise _CommandError('io')
                if output_file is not None:
                    try:
                        if output_file.stat().st_size > MAX_OUTPUT:
                            raise _CommandError('output')
                    except FileNotFoundError:
                        pass
                if time.monotonic() >= deadline:
                    raise _CommandError('timeout')
                cancel.wait(0.05)
        finally:
            try:
                _kill_process(process, job)
            finally:
                if reader is not None:
                    reader.join(timeout=1)
                if writer is not None:
                    writer.join(timeout=1)
                if reader is None or not reader.is_alive():
                    with suppress(OSError):
                        process.stdout.close()
                if process.stdin is not None and (writer is None or not writer.is_alive()):
                    with suppress(OSError):
                        process.stdin.close()
                with self._idle:
                    self._processes.pop(process, None)
                    self._idle.notify_all()
        if cancel.is_set():
            raise _CommandError('cancelled')
        if overflow.is_set():
            raise _CommandError('output')
        if io_error.is_set() or reader.is_alive():
            raise _CommandError('io')
        return process.returncode, bytes(output)

    def _command(self, config, arguments, timeout=AUTH_TIMEOUT, cancel=None, on_output=None):
        with tempfile.TemporaryDirectory(prefix='testpilot-codex-') as directory:
            cwd = Path(directory)
            return self._run([config.executable, *_auth_arguments(), *arguments], cwd=cwd,
                             env=_environment(config.home, cwd), timeout=timeout,
                             cancel=cancel, on_output=on_output)

    def _available(self, config):
        if not config.executable:
            return False
        try:
            info = Path(config.executable).stat()
            identity = (config.executable, info.st_mtime_ns, info.st_size)
            if self._version_identity == identity:
                return True
            code, output = self._command(config, ['--version'])
            if code == 0 and re.search(rb'(?m)^codex-cli 0\.167\.2\r?$', output):
                self._version_identity = identity
                return True
        except (OSError, _CommandError):
            pass
        return False

    def _status(self, config, state, message):
        return CodexStatus(available=bool(config and config.executable) and state != 'unavailable',
                           authenticated=state == 'authenticated', state=state,
                           model=config.model if config else None,
                           verification_uri=self._uri if state == 'pending' else None,
                           user_code=self._code if state == 'pending' else None,
                           message=message).model_dump()

    def _read_status(self, config):
        if not self._available(config):
            return self._status(config, 'unavailable', f'Нужен официальный Codex CLI {CLI_VERSION}. Проверьте TP_CODEX_BIN на сервере.')
        with self._lock:
            if self._state == 'pending':
                return self._status(config, 'pending', self._message)
        try:
            code, output = self._command(config, ['login', 'status'])
        except (OSError, _CommandError):
            return self._status(config, 'error', 'Не удалось проверить вход ChatGPT: Codex недоступен или истекло время ожидания.')
        text = _ANSI.sub('', output.decode('utf-8', errors='replace'))
        if code == 0 and 'Logged in using ChatGPT' in text.splitlines():
            with self._lock:
                self._state = None
                self._message = ''
            return self._status(config, 'authenticated', 'ChatGPT подключён. Подключение общее для всего стенда.')
        if code != 0 and 'Not logged in' not in text.splitlines():
            return self._status(config, 'error', 'Codex не смог проверить вход ChatGPT. Проверьте доступность сервиса и повторите вход.')
        with self._lock:
            if self._state == 'error':
                return self._status(config, 'error', self._message)
        return self._status(config, 'signed_out', 'ChatGPT не подключён. Эксперт может начать вход; подключение общее для стенда.')

    def status(self):
        with self._auth_lock:
            try:
                return self._read_status(_configuration())
            except (HTTPException, OSError) as error:
                message = error.detail if isinstance(error, HTTPException) else 'Каталог Codex недоступен. Проверьте CODEX_HOME на сервере.'
                return self._status(None, 'error', message)

    def login(self):
        with self._auth_lock:
            try:
                config = _configuration()
                current = self._read_status(config)
            except (HTTPException, OSError) as error:
                message = error.detail if isinstance(error, HTTPException) else 'Каталог Codex недоступен. Проверьте CODEX_HOME на сервере.'
                return self._status(None, 'error', message)
            if current['state'] in ('unavailable', 'pending', 'authenticated'):
                return current
            cancel = threading.Event()
            with self._lock:
                self._login_cancel = cancel
                self._state = 'pending'
                self._message = 'Codex запрашивает код входа ChatGPT. Подключение будет общим для всего стенда.'
                self._uri = self._code = None
                self._login_thread = threading.Thread(target=self._login_worker, args=(config, cancel), daemon=True)
                self._login_thread.start()
                return self._status(config, 'pending', self._message)

    def _login_worker(self, config, cancel):
        def publish(output):
            uri, code = _device_prompt(output)
            if uri and code:
                with self._lock:
                    if self._login_cancel is cancel and not cancel.is_set():
                        self._uri, self._code = uri, code
                        self._message = 'Откройте официальную ссылку и введите одноразовый код. Вход действует для всего стенда.'

        try:
            code, _ = self._command(config, ['login', '--device-auth'], timeout=LOGIN_TIMEOUT,
                                    cancel=cancel, on_output=publish)
            if code != 0:
                raise _CommandError('login')
            auth_code, output = self._command(config, ['login', 'status'], cancel=cancel)
            text = _ANSI.sub('', output.decode('utf-8', errors='replace'))
            if auth_code != 0 or 'Logged in using ChatGPT' not in text.splitlines():
                raise _CommandError('login')
            state, message = 'authenticated', 'ChatGPT подключён. Подключение общее для всего стенда.'
        except (_CommandError, OSError) as error:
            state = 'error'
            message = ('Время входа ChatGPT истекло. Начните вход заново.' if error.args == ('timeout',)
                       else 'Не удалось войти через ChatGPT. Проверьте доступность сервиса, разрешение device login в аккаунте и начните вход заново.')
        with self._lock:
            if self._login_cancel is cancel and not cancel.is_set():
                self._state, self._message = state, message
                self._uri = self._code = None

    def _cancel(self):
        with self._idle:
            if self._login_cancel is not None:
                self._login_cancel.set()
            for cancel in (*self._processes.values(), *self._generations):
                cancel.set()
            thread = self._login_thread
        if thread is not None:
            thread.join(timeout=5)
        with self._idle:
            stopped = self._idle.wait_for(lambda: not self._processes and not self._generations, timeout=5)
            if thread is not None and thread.is_alive():
                stopped = False
            if stopped:
                self._login_thread = self._login_cancel = None
                self._state = None
                self._uri = self._code = None
            return stopped

    def logout(self):
        with self._auth_lock:
            config = None
            try:
                config = _configuration()
                if not self._cancel():
                    return self._status(config, 'error', 'Не удалось остановить активный Codex. Повторите выход после остановки процесса.')
                if not self._available(config):
                    return self._status(config, 'unavailable', f'Для выхода нужен официальный Codex CLI {CLI_VERSION}. Проверьте TP_CODEX_BIN.')
                code, _ = self._command(config, ['logout'])
                if code != 0:
                    raise _CommandError('logout')
                return self._read_status(config)
            except (HTTPException, OSError, _CommandError):
                return self._status(config, 'error', 'Не удалось выйти из ChatGPT. Проверьте Codex на сервере и повторите выход.')

    def shutdown(self):
        with self._auth_lock:
            self._cancel()

    def generate(self, package, catalog, names, model_context=None):
        schema = _output_schema()
        system, content = provider.generation_messages(package, catalog, names, model_context, output_schema=schema)
        cancel = threading.Event()
        with self._auth_lock:
            config = _configuration()
            current = self._read_status(config)
            if not current['authenticated']:
                raise HTTPException(503, current['message'])
            with self._lock:
                self._generations.add(cancel)
        try:
            with tempfile.TemporaryDirectory(prefix='testpilot-codex-') as directory:
                cwd = Path(directory)
                schema_path, output_path = cwd / 'suggestion-schema.json', cwd / 'suggestion.json'
                schema_path.write_text(canonical(schema), encoding='utf-8')
                argv = [config.executable, 'exec', '--ignore-user-config', '--ignore-rules',
                        '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                        '--color', 'never', '--output-schema', str(schema_path),
                        '--output-last-message', str(output_path), *_auth_arguments(),
                        '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                        '-c', 'features.multi_agent=false', '-c', 'cloud.skills.enabled=false',
                        '-c', 'orchestrator.mcp.enabled=false', '-c', 'web_search="disabled"',
                        '-c', 'model_provider="openai"', '-c', 'approval_policy="never"']
                if config.model:
                    argv.extend(['--model', config.model])
                argv.append('-')
                prompt = (system + '\n\nКонтекст проекта (данные JSON):\n' + content).encode('utf-8')
                code, output = self._run(argv, cwd=cwd, env=_environment(config.home, cwd),
                                         timeout=config.timeout, cancel=cancel, input_data=prompt,
                                         limit=MAX_OUTPUT, output_file=output_path)
                if code != 0:
                    if re.search(rb'failed to refresh token[^\r\n]*\bstatus\s*=\s*401\b', output, re.IGNORECASE):
                        raise HTTPException(503, 'Codex не смог обновить вход ChatGPT. Обратитесь к эксперту: на странице «Нейросеть и проверки» отключите ChatGPT и войдите заново.')
                    raise HTTPException(502, 'Codex отклонил запрос. Проверьте вход ChatGPT, доступность выбранной модели и лимиты аккаунта.')
                with output_path.open('rb') as result:
                    answer = result.read(MAX_OUTPUT + 1)
                if len(answer) > MAX_OUTPUT:
                    raise HTTPException(502, 'Ответ Codex превышает допустимый размер.')
                return _suggestion(answer), config.model
        except _CommandError as error:
            if error.args == ('timeout',):
                raise HTTPException(504, 'Истекло время ожидания Codex. Процесс остановлен; черновик не создан.') from None
            if error.args == ('output',):
                raise HTTPException(502, 'Вывод Codex превышает допустимый размер. Процесс остановлен.') from None
            if error.args == ('cancelled',):
                raise HTTPException(503, 'Генерация Codex остановлена при выходе из ChatGPT или завершении сервиса.') from None
            raise HTTPException(503, 'Не удалось запустить Codex. Проверьте CLI и каталог учётных данных на сервере.') from None
        except OSError:
            raise HTTPException(502, 'Codex не вернул доступный результат. Проекты и каталог не изменены.') from None
        finally:
            with self._idle:
                self._generations.discard(cancel)
                self._idle.notify_all()


_client = CodexClient()


def status():
    return _client.status()


def login():
    return _client.login()


def logout():
    return _client.logout()


def generate(package, catalog, names, model_context=None):
    return _client.generate(package, catalog, names, model_context)


def shutdown():
    _client.shutdown()
