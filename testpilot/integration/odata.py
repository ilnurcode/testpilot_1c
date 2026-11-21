"""Безопасный OData-клиент для чтения и изменения данных в тестовой базе 1С:Предприятие."""
import argparse
import json
import os
import sys
from typing import Any
import httpx
from testpilot.integration.probe import Profile, container_access_urls, parse_metadata, validated_url
from testpilot.target_policy import redact_url

ALLOWED_PREFIXES = (
    'Catalog_',
    'Document_',
    'InformationRegister_',
    'AccumulationRegister_',
    'ChartOfAccounts_',
    'ChartOfCharacteristicTypes_',
)

class ODataError(Exception):
    """Базовое исключение OData 1C."""

class AuthenticationError(ODataError):
    """Ошибка аутентификации (401)."""

class PermissionDeniedError(ODataError):
    """Недостаточно прав для операции (403)."""

class NotFoundError(ODataError):
    """Сущность не найдена (404)."""

class UnsafeEntityError(ODataError):
    """Попытка обращения к запрещенной или невалидной сущности."""

class ODataClient:
    """Клиент стандартного OData интерфейса 1С:Предприятия."""

    def __init__(self, profile: Profile, transport: httpx.BaseTransport | None = None, timeout: float = 20.0, should_cancel=None):
        self.profile = profile
        self.transport = transport
        self.timeout = timeout
        self.should_cancel = should_cancel
        self.base_urls = [url.rstrip('/') for url in container_access_urls(profile.odata_url)]
        self.base_url = self.base_urls[0]
        # Адрес, который реально ответил: при откате с host.docker.internal это исходный loopback.
        self.last_base_url = self.base_url

    def _client(self) -> httpx.Client:
        headers = {
            'Accept': 'application/json;odata.metadata=minimal',
            'Content-Type': 'application/json;charset=utf-8',
        }
        auth = None
        if self.profile.authorization:
            headers['Authorization'] = self.profile.authorization
        elif self.profile.username:
            auth = (self.profile.username, self.profile.password or '')

        verify = self.profile.ca_file or True
        return httpx.Client(
            headers=headers,
            auth=auth,
            verify=verify,
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send only to a policy-checked exact origin; foreign redirects are rejected before following."""
        from testpilot.target_policy import check_credentials_target, check_target, TargetDenied
        for index, base in enumerate(self.base_urls):
            if self.should_cancel and self.should_cancel():
                raise ODataError('Отменено до отправки следующего OData-запроса')
            try:
                target = check_target(f'{base}{path}', purpose='odata_url')
                if self.profile.username or self.profile.authorization:
                    check_credentials_target(base, target)
            except TargetDenied as error:
                raise ODataError(f'Адрес OData отклонён: {error}') from error
            try:
                with self._client() as client:
                    response = client.request(method, target, **kwargs)
            except httpx.ConnectError:
                if index == len(self.base_urls) - 1:
                    raise
                continue
            location = response.headers.get('location')
            if response.is_redirect and location:
                from urllib.parse import urljoin, urlsplit
                target = urljoin(base, location)
                if urlsplit(target).netloc != urlsplit(base).netloc:
                    raise ODataError('OData redirect на сторонний origin запрещён')
            self.last_base_url = base
            return response

    def validate_entity_set(self, entity_set: str) -> str:
        name = entity_set.strip()
        if not name or not any(name.startswith(p) for p in ALLOWED_PREFIXES):
            raise UnsafeEntityError(f"Сущность '{name}' не входит в список разрешенных таблиц 1С ({', '.join(ALLOWED_PREFIXES)})")
        if any(c in name for c in '/\\?&#; '):
            raise UnsafeEntityError(f"Недопустимые символы в имени сущности: {name}")
        return name

    def _extract_error(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            if 'odata.error' in payload and 'message' in payload['odata.error']:
                return payload['odata.error']['message'].get('value', response.text)
            if 'error' in payload and isinstance(payload['error'], dict):
                return payload['error'].get('message', response.text)
        except Exception:
            pass
        return response.text[:500]

    def _handle_response(self, response: httpx.Response) -> Any:
        if 300 <= response.status_code < 400:
            location = response.headers.get('location') or ''
            raise ODataError(
                f'Редирект публикации отклонён (HTTP {response.status_code} на {redact_url(location)}): '
                'учётные данные не пересылаются. Укажите прямой URL публикации.'
            )
        if response.status_code == 401:
            raise AuthenticationError("Требуется аутентификация в 1С (HTTP 401)")
        if response.status_code == 403:
            raise PermissionDeniedError("Доступ к ресурсу 1С запрещен (HTTP 403)")
        if response.status_code == 404:
            raise NotFoundError("Запрошенный объект не найден в 1С (HTTP 404)")
        if response.status_code >= 400:
            err = self._extract_error(response)
            raise ODataError(f"Ошибка 1С OData (HTTP {response.status_code}): {err}")
        if response.status_code == 204:
            return None
        return response.json()

    def get_metadata(self) -> list[dict]:
        """Чтение и разбор $metadata сервиса 1С."""
        r = self._request('GET', '/$metadata', headers={'Accept': 'application/xml'})
        if r.status_code != 200:
            self._handle_response(r)
        return parse_metadata(r.content)

    def get_entities(
        self,
        entity_set: str,
        select: list[str] | None = None,
        filter_query: str | None = None,
        top: int = 100,
        skip: int = 0
    ) -> list[dict]:
        """Получение списка записей сущности 1С."""
        name = self.validate_entity_set(entity_set)
        params: dict[str, Any] = {'$top': min(top, 1000), '$skip': max(skip, 0)}
        if select:
            params['$select'] = ','.join(s.strip() for s in select)
        if filter_query:
            params['$filter'] = filter_query

        r = self._request('GET', f'/{name}', params=params)
        data = self._handle_response(r)
        return data.get('value', []) if isinstance(data, dict) else []

    def get_count(self, entity_set: str, filter_query: str | None = None) -> int:
        """Точное число записей через OData /$count, без усечения top."""
        name = self.validate_entity_set(entity_set)
        params: dict[str, Any] = {}
        if filter_query:
            params['$filter'] = filter_query
        response = self._request('GET', f'/{name}/$count', params=params,
                                 headers={'Accept': 'text/plain'})
        if response.status_code != 200:
            self._handle_response(response)
        try:
            return int(response.text.strip())
        except ValueError as error:
            raise ODataError('OData $count вернул не целое число') from error

    def get_entity(self, entity_set: str, key: str) -> dict:
        """Получение одного объекта по ключу / GUID."""
        name = self.validate_entity_set(entity_set)
        path = f"/{name}(guid'{key}')" if '-' in key and len(key) == 36 else f"/{name}('{key}')"
        return self._handle_response(self._request('GET', path))

    def create_entity(self, entity_set: str, data: dict) -> dict:
        """Создание записи в тестовой базе 1С."""
        name = self.validate_entity_set(entity_set)
        return self._handle_response(self._request('POST', f'/{name}', json=data))

    def update_entity(self, entity_set: str, key: str, data: dict) -> dict:
        """Обновление записи в тестовой базе 1С."""
        name = self.validate_entity_set(entity_set)
        path = f"/{name}(guid'{key}')" if '-' in key and len(key) == 36 else f"/{name}('{key}')"
        return self._handle_response(self._request('PATCH', path, json=data))

    def delete_entity(self, entity_set: str, key: str) -> bool:
        """Удаление тестовой записи в 1С."""
        name = self.validate_entity_set(entity_set)
        path = f"/{name}(guid'{key}')" if '-' in key and len(key) == 36 else f"/{name}('{key}')"
        self._handle_response(self._request('DELETE', path))
        return True

def main():
    parser = argparse.ArgumentParser(description="OData-клиент для тестовой базы 1С:Предприятия")
    parser.add_argument('--entity', help="Имя EntitySet (например, Catalog_Номенклатура)")
    parser.add_argument('--limit', type=int, default=5, help="Лимит строк")
    args = parser.parse_args()

    profile = Profile.from_environment()
    if not profile:
        print("Переменная TP_1C_ODATA_URL не задана в окружении", file=sys.stderr)
        sys.exit(1)

    client = ODataClient(profile)
    try:
        if args.entity:
            items = client.get_entities(args.entity, top=args.limit)
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            meta = client.get_metadata()
            print(f"Доступные сущности 1С ({len(meta)}):")
            for m in meta[:20]:
                print(f" - {m['name']} ({m['entity_type']})")
    except Exception as e:
        print(f"Ошибка OData 1С: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
