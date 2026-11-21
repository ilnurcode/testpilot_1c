"""Секреты подключения: DPAPI Windows и portable AES-GCM без plaintext fallback."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import os
from pathlib import Path
import stat

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCHEME_DPAPI = 'dpapi-user-v1'
SCHEME_PORTABLE_AESGCM = 'portable-aesgcm-v1'
ENTROPY = b'testpilot-1c/connection-secret/v1'
PORTABLE_AAD_PREFIX = b'testpilot-1c/project-secret/v1\x00'
PORTABLE_KEY_ENV = 'TP_PORTABLE_SECRET_KEY_FILE'
PROTECTOR_ENV = 'TP_SECRET_PROTECTOR'
CRYPTPROTECT_UI_FORBIDDEN = 0x1
PORTABLE_KEY_BYTES = 32
PORTABLE_NONCE_BYTES = 12
MAX_PORTABLE_KEY_FILE_BYTES = 128
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SecretsError(RuntimeError):
    """Секреты недоступны: нет протектора, секрет не расшифровывается или формат неизвестен."""


class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _take(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


class DpapiProtector:
    """DPAPI текущего пользователя Windows; entropy фиксирует назначение секрета."""

    scheme = SCHEME_DPAPI

    def __init__(self) -> None:
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def seal(self, plaintext: str, *, context: bytes | None = None) -> str:
        source = _blob(plaintext.encode('utf-8'))
        entropy = _blob(ENTROPY)
        output = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source), 'TestPilot 1C connection secret', ctypes.byref(entropy),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        if not ok:
            raise SecretsError(f'DPAPI не зашифровал секрет (код {ctypes.GetLastError()}). Секрет не сохранён.')
        return base64.b64encode(_take(output)).decode('ascii')

    def unseal(self, blob: str, *, context: bytes | None = None) -> str:
        try:
            raw = base64.b64decode(blob.encode('ascii'), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretsError('Сохранённый секрет повреждён: ожидался base64 от DPAPI.') from exc
        source = _blob(raw)
        entropy = _blob(ENTROPY)
        output = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        if not ok:
            raise SecretsError(
                'Секрет не расшифровывается на этом компьютере/пользователе (DPAPI). '
                'Перенос на другой профиль требует повторного ввода пароля владельцем; '
                'зашифрованная запись не удаляется автоматически.'
            )
        try:
            return _take(output).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SecretsError('Расшифрованный секрет не является текстом UTF-8.') from exc


def _external_key_path(raw: str | os.PathLike[str] | None = None) -> Path:
    value = os.fspath(raw) if raw is not None else os.environ.get(PORTABLE_KEY_ENV, '')
    if not value:
        raise SecretsError('Portable key material недоступен: нужен внешний key file.')
    path = Path(value)
    if not path.is_absolute():
        raise SecretsError('Portable key material недоступен: key file должен иметь абсолютный путь.')
    try:
        path.resolve().relative_to(REPOSITORY_ROOT)
    except ValueError:
        return path
    raise SecretsError('Portable key material недоступен: key file не может находиться в checkout.')



def _read_portable_key(path: Path) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise SecretsError('Portable key material недоступен.') from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SecretsError('Portable key material небезопасен.')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecretsError('Portable key material недоступен.') from exc
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode):
            raise SecretsError('Portable key material небезопасен.')
        if os.name != 'nt':
            if current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) & 0o077:
                raise SecretsError('Portable key material имеет небезопасные права доступа.')
        data = os.read(descriptor, MAX_PORTABLE_KEY_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_PORTABLE_KEY_FILE_BYTES:
        raise SecretsError('Portable key material имеет неверный формат.')
    if data.endswith(b'\n'):
        data = data[:-1]
    if not data or b'\n' in data or b'\r' in data:
        raise SecretsError('Portable key material имеет неверный формат.')
    try:
        key = base64.b64decode(data, altchars=b'-_', validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretsError('Portable key material имеет неверный формат.') from exc
    if len(key) != PORTABLE_KEY_BYTES:
        raise SecretsError('Portable key material имеет неверный формат.')
    return key


def portable_key() -> bytes:
    return _read_portable_key(_external_key_path())


def create_portable_key(path: str | os.PathLike[str]) -> None:
    """Явно создаёт новый external key file; вызов старта приложения его не делает."""
    destination = _external_key_path(path)
    if not destination.parent.is_dir():
        raise SecretsError('Каталог для portable key material отсутствует.')
    payload = base64.urlsafe_b64encode(os.urandom(PORTABLE_KEY_BYTES)) + b'\n'
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise SecretsError('Portable key material не создан.') from exc
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError('не удалось записать portable key material')
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        os.close(descriptor)
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise SecretsError('Portable key material не создан.') from exc
    else:
        os.close(descriptor)


def project_secret_context(project_id: str) -> bytes:
    if not project_id:
        raise SecretsError('Portable secret требует контекст проекта.')
    return PORTABLE_AAD_PREFIX + project_id.encode('utf-8')


class PortableAesGcmProtector:
    """AES-256-GCM с externally provisioned key; переносим только key file и ciphertext."""

    scheme = SCHEME_PORTABLE_AESGCM

    def seal(self, plaintext: str, *, context: bytes | None = None) -> str:
        if context is None:
            raise SecretsError('Portable secret требует контекст проекта.')
        nonce = os.urandom(PORTABLE_NONCE_BYTES)
        encrypted = AESGCM(portable_key()).encrypt(nonce, plaintext.encode('utf-8'), context)
        return base64.urlsafe_b64encode(nonce + encrypted).decode('ascii')

    def unseal(self, blob: str, *, context: bytes | None = None) -> str:
        if context is None:
            raise SecretsError('Portable secret требует контекст проекта.')
        try:
            raw = base64.b64decode(blob.encode('ascii'), altchars=b'-_', validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretsError('Сохранённый portable secret повреждён.') from exc
        if len(raw) <= PORTABLE_NONCE_BYTES:
            raise SecretsError('Сохранённый portable secret повреждён.')
        try:
            plaintext = AESGCM(portable_key()).decrypt(
                raw[:PORTABLE_NONCE_BYTES], raw[PORTABLE_NONCE_BYTES:], context,
            )
        except InvalidTag as exc:
            raise SecretsError('Portable secret не расшифровывается с текущим key material.') from exc
        try:
            return plaintext.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SecretsError('Расшифрованный секрет не является текстом UTF-8.') from exc


def _mode() -> str:
    mode = (os.environ.get(PROTECTOR_ENV) or 'auto').strip().lower()
    if mode not in {'auto', 'dpapi', 'portable'}:
        raise SecretsError('Настройка протектора секретов недопустима.')
    return mode


def _dpapi() -> DpapiProtector:
    if os.name != 'nt':
        raise SecretsError('DPAPI недоступен на этой платформе.')
    return DpapiProtector()


def active_protector() -> DpapiProtector | PortableAesGcmProtector:
    """Выбирает протектор новых записей, не изменяя формат уже сохранённых."""
    mode = _mode()
    if mode == 'dpapi' or (mode == 'auto' and os.name == 'nt'):
        return _dpapi()
    return PortableAesGcmProtector()


def protector_for_scheme(scheme: str) -> DpapiProtector | PortableAesGcmProtector:
    if scheme == SCHEME_DPAPI:
        return _dpapi()
    if scheme == SCHEME_PORTABLE_AESGCM:
        return PortableAesGcmProtector()
    raise SecretsError(
        'Схема сохранённого секрета не поддерживается этим профилем развёртывания; '
        'миграция схем выполняется отдельным согласованным действием.'
    )


def protector() -> DpapiProtector | PortableAesGcmProtector:
    """Совместимое имя active_protector для статуса и старых внутренних вызовов."""
    return active_protector()


def available() -> bool:
    try:
        impl = active_protector()
        if impl.scheme == SCHEME_PORTABLE_AESGCM:
            portable_key()
    except SecretsError:
        return False
    return True


def seal(plaintext: str, *, context: bytes | None = None) -> tuple[str, str]:
    """Шифрует пароль: (envelope, scheme). Пустой пароль не шифруется."""
    if not plaintext:
        raise SecretsError('Пустой пароль не шифруется: секрет отсутствует.')
    impl = active_protector()
    return f'{impl.scheme}:{impl.seal(plaintext, context=context)}', impl.scheme


def open_sealed(envelope: str, *, context: bytes | None = None) -> str:
    """Расшифровывает envelope; чужая схема/ключ — SecretsError без подстановки текста."""
    if not envelope or ':' not in envelope:
        raise SecretsError('Неизвестный формат сохранённого секрета: ожидается схема:значение.')
    scheme, _, blob = envelope.partition(':')
    return protector_for_scheme(scheme).unseal(blob, context=context)


def status() -> dict:
    """Состояние без секретов, ключей и путей key file."""
    try:
        impl = active_protector()
        if impl.scheme == SCHEME_PORTABLE_AESGCM:
            portable_key()
    except SecretsError as exc:
        # reason — уже безопасный текст отказа (без key material, путей и plaintext).
        return {'protector': None, 'available': False, 'scope': None, 'portable': None,
                'reason': str(exc)}
    if impl.scheme == SCHEME_DPAPI:
        return {'protector': SCHEME_DPAPI, 'available': True,
                'scope': 'windows-user-profile', 'portable': False}
    return {'protector': SCHEME_PORTABLE_AESGCM, 'available': True,
            'scope': 'external-key-file', 'portable': True}
