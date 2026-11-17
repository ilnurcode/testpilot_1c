# Один живой прогон на DemoTrd (ручной запуск владельцем)

Вход и навигация на публикации 1С: одна попытка входа, только демо-база, без `allow_mutations`/`strict_read_only`.

## Предусловия

- Стек поднят (`scripts/compose.sh up -d --wait`), `GET /health` = 200 у web/impact/intelligence.
- `curl -s -o /dev/null -w '%{http_code}' http://localhost:8088/DemoTrd/` → `200`.
- Внутри контейнера публикация видна как `host.docker.internal:8088`: раннер переписывает loopback
  и откатывается на исходный адрес только при connection-level ошибке.

## 1. Проект

`PUT /api/v1/projects/{id}/connection` (Basic Auth, роль analyst): `{"odata_url": "...",
"web_url": "http://localhost:8088/DemoTrd", "auth_username": "Администратор", "auth_password": ""}`.
Пустой пароль допустим, если так настроена демо-база. Пароль хранится в БД открытым текстом — только
синтетическая учётная запись, без переиспользования реальной.

## 2. План

`POST /api/v1/ai/suites` с `{"package_id": "...", "project_id": "...", "provider": "local"}`
(пакет — результат `POST /api/v1/analyses/{aid}/deliver`); в ответе `proposals` с `engine`.
`POST /api/v1/ai/suites/{sid}/approve` с `{"reason": "...", "selected_proposals": [<индекс>]}` —
только `engine=playwright`, `mutating=false`, без `fill`-шагов.

## 3. Запуск

`POST /api/v1/ai/suites/{sid}/run-1c` без параметров (`allow_mutations`/`strict_read_only` не передаются).
Форма появляется примерно через 15 с (`TP_1C_LOGIN_WAIT_SECONDS`, по умолчанию 25), после клика — grace
(`TP_1C_LOGIN_GRACE_SECONDS`); худший случай около 95 с. Повторный `run-1c` вернёт сохранённый результат
и вход заново не выполнит.

## 4. Результат

`status`, `counts`, `message`, `evidence_id`; для playwright — `login.{stage, user_filled,
password_filled, clicked, form_visible_after}` (значения полей не логируются) и `screenshot_unavailable`,
если снимок не удался. Скриншот: `GET /api/v1/ai/evidence/{evidence_id}` (Basic Auth, PNG).

## 5. Исходы

- `passed=1` — вход и навигация выполнены, раздел найден.
- `error` + `stage=after_click` + `form_visible_after=true` — клик не привёл к входу: смотреть скриншот.
- `error` + `stage=submit` — кнопка «Войти» не найдена или не сработала.
- `failed` — шаг навигации не нашёл раздел: проверить план и публикацию.
