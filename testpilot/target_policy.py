"""Явная политика сетевых целей: allowlist, local opt-in, отказ до отправки, redaction.

Применяется к адресам, которые задаёт пользователь (публикация 1С, OData, веб-клиент),
до любого сетевого вызова. Секреты и токены не попадают в сообщения: для ошибок
используется только безопасный origin, значения query/path не печатаются.

Переменные окружения:
  TP_TARGET_ALLOWLIST    — строгий список `host[:port]` или `[v6][:port]` через запятую.
                           Запись без порта разрешает только штатные порты схемы (80/443).
                           Некорректная запись — fail-closed (запрос не отправляется).
  TP_ALLOW_LOCAL_TARGETS — 1/true/yes разрешает loopback-адреса (локальный стенд).
"""

from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urljoin, urlsplit

LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', 'host.docker.internal'}
DEFAULT_PORTS = {'http': 80, 'https': 443}
HOST_ONLY_PORTS = frozenset(DEFAULT_PORTS.values())
REDACTED = '…'
HOST_PATTERN = re.compile(r'^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$')


class TargetDenied(ValueError):
    """Цель отклонена до отправки запроса."""


def _invalid_entry(reason: str) -> TargetDenied:
    # Значение записи не печатаем: в конфигурацию могут попасть лишние данные.
    return TargetDenied(f'TP_TARGET_ALLOWLIST: некорректная запись ({reason}). Запрос не отправлен.')


def _parse_port(value: str | None) -> int | None:
    if value in (None, ''):
        return None
    if not value.isdigit():
        raise _invalid_entry('порт должен быть числом')
    port = int(value)
    if not 1 <= port <= 65535:
        raise _invalid_entry('порт вне диапазона 1..65535')
    return port


def _parse_entry(raw: str) -> tuple[str, int | None]:
    text = raw.strip().lower()
    if not text:
        raise _invalid_entry('пустая запись')
    if text.startswith('['):
        host, sep, rest = text[1:].partition(']')
        if not sep:
            raise _invalid_entry('незакрытый IPv6 в скобках')
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise _invalid_entry('некорректный IPv6') from exc
        if rest and not rest.startswith(':'):
            raise _invalid_entry('после IPv6 ожидается :port')
        return host, _parse_port(rest[1:] if rest else None)
    if text.count(':') > 1:
        raise _invalid_entry('IPv6 указывайте в скобках: [addr]:port')
    host, sep, port = text.partition(':')
    if not host:
        raise _invalid_entry('пустой host')
    try:
        ipaddress.IPv4Address(host)
        valid_host = True
    except ValueError:
        valid_host = bool(HOST_PATTERN.fullmatch(host)) and '..' not in host
    if not valid_host:
        raise _invalid_entry('некорректный host')
    return host, _parse_port(port if sep else None)


def allowlist() -> frozenset[tuple[str, int | None]]:
    """Разобранный allowlist. Любая некорректная запись — TargetDenied (fail-closed)."""
    entries: set[tuple[str, int | None]] = set()
    for item in (os.environ.get('TP_TARGET_ALLOWLIST') or '').split(','):
        if not item.strip():
            continue
        entries.add(_parse_entry(item))
    return frozenset(entries)


def local_allowed() -> bool:
    return (os.environ.get('TP_ALLOW_LOCAL_TARGETS') or '').strip().lower() in {'1', 'true', 'yes'}


def effective_port(parts) -> int:
    """Эффективный порт с учётом схемы; malformed port — отказ без утечки значения."""
    try:
        if parts.port is not None:
            return parts.port
    except ValueError as exc:
        raise TargetDenied('Цель подключения: некорректный порт в URL. Запрос не отправлен.') from exc
    scheme = (parts.scheme or '').lower()
    return DEFAULT_PORTS[scheme]


def safe_origin(url: str) -> str:
    """Только безопасный origin для сообщений: без userinfo, path, query и fragment."""
    if not url or not isinstance(url, str):
        return '<нет адреса>'
    try:
        parts = urlsplit(url.strip())
        scheme = (parts.scheme or '').lower()
        host = parts.hostname or ''
        if scheme not in DEFAULT_PORTS or not host:
            return '<некорректный URL>'
        port = effective_port(parts)
        suffix = '' if port == DEFAULT_PORTS[scheme] else f':{port}'
        return f'{scheme}://{host}{suffix}'
    except (TargetDenied, ValueError):
        return '<некорректный URL>'


def redact_url(value: str) -> str:
    """Совместимое имя: для логов/ошибок отдаём только origin."""
    return safe_origin(value)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    secret_names = {'authorization', 'cookie', 'set-cookie', 'proxy-authorization', 'x-api-key'}
    return {name: (REDACTED if name.lower() in secret_names else value) for name, value in headers.items()}


def _origin(parts) -> tuple[str, str, int]:
    return (parts.scheme or '').lower(), (parts.hostname or '').lower(), effective_port(parts)


def check_target(url: str, *, purpose: str = 'цель подключения') -> str:
    """Проверяет адрес до отправки. Возвращает URL без завершающего '/', либо TargetDenied."""
    if not url or not isinstance(url, str):
        raise TargetDenied(f'Не задан адрес ({purpose}).')
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or '').lower()
    if scheme not in DEFAULT_PORTS:
        raise TargetDenied(f'{purpose}: нужен http/https ({safe_origin(url)}).')
    if parts.username or parts.password:
        raise TargetDenied(f'{purpose}: URL не должен содержать учётные данные ({safe_origin(url)}).')
    host = (parts.hostname or '').lower()
    if not host:
        raise TargetDenied(f'{purpose}: в URL нет хоста.')
    port = effective_port(parts)
    if host in LOCAL_HOSTS:
        if not local_allowed():
            raise TargetDenied(
                f'{purpose}: loopback-адрес {host} разрешается только явным TP_ALLOW_LOCAL_TARGETS=1.'
            )
        return url.strip().rstrip('/')
    entries = allowlist()  # может бросить TargetDenied до любого запроса
    explicit = (host, port) in entries
    host_only = (host, None) in entries and port in HOST_ONLY_PORTS
    if explicit or host_only:
        return url.strip().rstrip('/')
    raise TargetDenied(
        f'{purpose}: {safe_origin(url)} не входит в TP_TARGET_ALLOWLIST (эффективный порт {port}); '
        'запрос не отправлен.'
    )


def check_credentials_target(origin_url: str, target_url: str) -> str:
    """Учётные данные отправляются только на тот же origin (эффективный порт, не None)."""
    if _origin(urlsplit(origin_url)) != _origin(urlsplit(target_url)):
        raise TargetDenied(
            f'Учётные данные не пересылаются на другой origin ({safe_origin(origin_url)} → {safe_origin(target_url)}).'
        )
    return target_url


def check_redirect(current_url: str, location: str | None, *, allow_cross_origin: bool = False) -> str | None:
    """Цель редиректа проверяется до перехода.

    По умолчанию cross-origin редирект отклоняется: клиент не должен автоматически
    переносить учётные данные на другой origin. allow_cross_origin=True разрешает
    только переход без credentials и только для явно разрешённой цели.
    """
    if not location:
        return None
    resolved = urljoin(current_url, location)
    if _origin(urlsplit(resolved)) == _origin(urlsplit(current_url)):
        return resolved
    if not allow_cross_origin:
        raise TargetDenied(
            f'Редирект на другой origin отклонён ({safe_origin(current_url)} → {safe_origin(resolved)}): '
            'учётные данные не пересылаются.'
        )
    check_target(resolved, purpose='цель редиректа')
    return resolved
