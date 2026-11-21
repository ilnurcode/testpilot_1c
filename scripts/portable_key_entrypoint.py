"""Entrypoint: приватная копия portable-ключа внутри контейнера.

NOTE (WIP): незавершённая работа, не готовое решение.
- Файл нигде не подключён: ни `compose.yaml`, ни `testpilot/`, ни тесты, ни документация
  на него не ссылаются; прогонов и проверок нет, работоспособность доставки ключа не
  подтверждена.
- Открытый вопрос модели угроз: образ не задаёт `USER`, поэтому процесс в контейнере —
  root, и `chmod 0600` сам по себе не ограничивает доступ других сервисов и процессов
  внутри контейнера. Перед использованием нужно ограничить ключ только сервисом
  `impact`/`intelligence`, определить UID/GID и проверить, что копия недоступна
  остальным.
- При этом проверку прав в `testpilot/secrets.py` ослаблять нельзя.

Docker Desktop на Windows отдаёт файл, смонтированный с хоста, с mode 0777 и чужим
uid. Строгая проверка `testpilot/secrets.py` (regular file, `st_uid == euid`,
отсутствие group/other-битов) на таком источнике отказывает законно — ослаблять её
нельзя. Поэтому источник монтируется read-only, ключ проверяется и копируется в
приватный каталог на tmpfs с `0600` и владельцем процесса, а приложению через
`TP_PORTABLE_SECRET_KEY_FILE` передаётся путь к копии. Источник защищён средствами
хоста (ACL Windows), копия живёт только в памяти контейнера.

Переменные окружения:
  TP_PORTABLE_SECRET_KEY_SOURCE — read-only смонтированный key file (обязательна)
  TP_PORTABLE_SECRET_KEY_FILE   — путь приватной копии внутри контейнера (обязательна)

Коды выхода: 78 — непригодная конфигурация или key material, 74 — ошибка ввода-вывода.
Содержимое ключа не печатается и в сообщениях не появляется.

    python scripts/portable_key_entrypoint.py <команда...>

Спецификация key material намеренно продублирована: скрипт запускается до приложения
и не должен зависеть от установленного пакета. Канон — `testpilot/secrets.py`
(`_read_portable_key`): один 32-byte ключ в URL-safe base64 без переводов строк.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import stat
import sys

SOURCE_ENV = 'TP_PORTABLE_SECRET_KEY_SOURCE'
DESTINATION_ENV = 'TP_PORTABLE_SECRET_KEY_FILE'
KEY_BYTES = 32
MAX_KEY_FILE_BYTES = 128
EXIT_CONFIG = 78
EXIT_IO = 74
FORMAT_REASON = (
    'Portable key material имеет неверный формат: ожидается один 32-byte ключ '
    'в URL-safe base64 без переводов строк.'
)


class KeyPreparationError(RuntimeError):
    """Ключ не подготовлен; текст пригоден для лога и не содержит key material."""

    def __init__(self, reason: str, exit_code: int = EXIT_CONFIG) -> None:
        super().__init__(reason)
        self.exit_code = exit_code


def _read_source(source: Path) -> bytes:
    try:
        before = os.lstat(source)
    except OSError as exc:
        raise KeyPreparationError(
            'Источник portable-ключа не найден: проверьте TP_PORTABLE_SECRET_KEY_SOURCE '
            'и монтирование файла.'
        ) from exc
    if stat.S_ISLNK(before.st_mode):
        raise KeyPreparationError('Источник portable-ключа — символическая ссылка; монтируйте обычный файл.')
    if not stat.S_ISREG(before.st_mode):
        raise KeyPreparationError(
            'Источник portable-ключа не является обычным файлом: проверьте, что '
            'TP_PORTABLE_SECRET_HOST_FILE указывает на существующий файл.'
        )
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise KeyPreparationError(
            'Источник portable-ключа не читается; источник должен быть смонтирован read-only.',
            EXIT_IO,
        ) from exc
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode):
            raise KeyPreparationError('Источник portable-ключа не является обычным файлом.')
        if current.st_size > MAX_KEY_FILE_BYTES:
            raise KeyPreparationError('Portable key material имеет неверный формат: файл больше 128 байт.')
        data = os.read(descriptor, MAX_KEY_FILE_BYTES + 1)
    except OSError as exc:
        raise KeyPreparationError('Источник portable-ключа не читается.', EXIT_IO) from exc
    finally:
        os.close(descriptor)
    if len(data) > MAX_KEY_FILE_BYTES:
        raise KeyPreparationError('Portable key material имеет неверный формат: файл больше 128 байт.')
    return data


def _decode_key(data: bytes) -> bytes:
    if data.endswith(b'\n'):
        data = data[:-1]
    if not data or b'\n' in data or b'\r' in data:
        raise KeyPreparationError(FORMAT_REASON)
    try:
        key = base64.b64decode(data, altchars=b'-_', validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise KeyPreparationError(FORMAT_REASON) from exc
    if len(key) != KEY_BYTES:
        raise KeyPreparationError(FORMAT_REASON)
    return key


def _own_to_process(path: Path) -> None:
    if os.name == 'nt':
        return
    current = os.lstat(path)
    if current.st_uid == os.geteuid() and current.st_gid == os.getegid():
        return
    try:
        os.chown(path, os.geteuid(), os.getegid())
    except OSError as exc:
        raise KeyPreparationError(
            'Не удалось назначить владельца приватной копии ключа; запуск остановлен.',
            EXIT_IO,
        ) from exc


def _ensure_private_dir(parent: Path) -> None:
    if not parent.exists():
        try:
            parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise KeyPreparationError(
                'Не удалось создать приватный каталог для копии ключа; каталог должен быть на tmpfs.',
                EXIT_IO,
            ) from exc
    current = os.lstat(parent)
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise KeyPreparationError('Каталог для копии ключа небезопасен: ожидается обычный каталог.')
    if os.name != 'nt':
        os.chmod(parent, 0o700)
    _own_to_process(parent)


def _verify_destination(destination: Path) -> None:
    current = os.lstat(destination)
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise KeyPreparationError('Приватная копия ключа небезопасна: ожидается обычный файл.')
    if os.name != 'nt':
        if current.st_uid != os.geteuid():
            raise KeyPreparationError('Приватная копия ключа принадлежит другому пользователю; запуск остановлен.')
        if stat.S_IMODE(current.st_mode) & 0o077:
            raise KeyPreparationError('Приватная копия ключа доступна группе или остальным; запуск остановлен.')


def prepare_portable_key(source: Path, destination: Path) -> Path:
    """Проверяет источник и публикует приватную копию ключа; возвращает путь копии."""
    source, destination = Path(source), Path(destination)
    if not source.is_absolute() or not destination.is_absolute():
        raise KeyPreparationError('Пути portable-ключа должны быть абсолютными.')
    if source == destination:
        raise KeyPreparationError('Копия ключа не может совпадать с источником: нужен отдельный путь на tmpfs.')
    key = _decode_key(_read_source(source))

    parent = destination.parent
    _ensure_private_dir(parent)
    payload = base64.urlsafe_b64encode(key) + b'\n'
    temporary = parent / f'.{destination.name}.tmp-{os.getpid()}'
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise KeyPreparationError('Не удалось создать приватную копию ключа.', EXIT_IO) from exc
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError('не удалось записать приватную копию ключа')
            written += count
        os.fsync(descriptor)
        if os.name != 'nt':
            os.fchmod(descriptor, 0o600)
    except OSError as exc:
        os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise KeyPreparationError('Не удалось записать приватную копию ключа.', EXIT_IO) from exc
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, destination)
        _verify_destination(destination)
        if os.name != 'nt':
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise KeyPreparationError('Не удалось опубликовать приватную копию ключа.', EXIT_IO) from exc
    finally:
        if temporary.exists():
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return destination


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    raw_source = (os.environ.get(SOURCE_ENV) or '').strip()
    raw_destination = (os.environ.get(DESTINATION_ENV) or '').strip()
    if not raw_source:
        print(f'portable-key: {SOURCE_ENV} не задан; portable-ключ не подготовлен, запуск остановлен.',
              file=sys.stderr)
        return EXIT_CONFIG
    if not raw_destination:
        print(f'portable-key: {DESTINATION_ENV} не задан; приложению некуда передать копию ключа, '
              'запуск остановлен.', file=sys.stderr)
        return EXIT_CONFIG
    destination = Path(raw_destination)
    try:
        prepare_portable_key(Path(raw_source), destination)
    except KeyPreparationError as exc:
        print(f'portable-key: {exc}', file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        # Причина без key material: печатаем только errno-описание.
        print(f'portable-key: ошибка ввода-вывода при подготовке ключа ({exc.strerror or type(exc).__name__}); '
              'запуск остановлен.', file=sys.stderr)
        return EXIT_IO
    os.environ[DESTINATION_ENV] = str(destination)
    print(f'portable-key: приватная копия ключа подготовлена по пути {destination}; '
          'источник прочитан только из read-only монтирования.', flush=True)
    if not arguments:
        return 0
    try:
        os.execvp(arguments[0], arguments)
    except OSError as exc:
        print(f'portable-key: не удалось запустить команду контейнера ({exc.strerror or type(exc).__name__}).',
              file=sys.stderr)
        return EXIT_IO
    return EXIT_IO


if __name__ == '__main__':
    raise SystemExit(main())
