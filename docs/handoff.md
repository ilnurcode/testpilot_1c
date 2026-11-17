# Передача проекта

Короткая сводка состояния. Подробности — в [api.md](api.md), [rules.md](rules.md), [security.md](security.md), [acceptance.md](acceptance.md).

## Состояние БД после инцидента

2026-11-17 при локальной проверке CI-джобы была выполнена `docker compose down -v`, которая удалила рабочую БД (`testpilot_pgdata`: 33 проекта, 39 загрузок, 45 suite, 42 записи `code_audit`, 107 evidence) и `testpilot_codex_auth`. Подробности и результаты поиска бэкапов — [incident-2026-11-17.md](incident-2026-11-17.md). До восстановления данных приёмка на этой машине покажет 0 проектов; для проверки используйте чистый проект `-p testpilot-ci`. Пересоздание демо-данных по шагам — [recreate-demo.md](recreate-demo.md). Репозиторий и тестовые ассеты не пострадали.

## Состояние

- Коммит кода: `3b79162` (последний перед этим документом), ветка `main`; точный HEAD — `git log -1 --oneline`.
- Тесты: число печатает `docker compose exec -T intelligence python -m pytest tests/ -q` и `scripts/acceptance.sh`; в документе не фиксируется.
- Миграции: `0008 (head)` (`docker compose exec -T intelligence alembic current`).
- Compose: `db`, `impact`, `intelligence`, `web` — Up (healthy); `migrate` — Exited (0).
- Dev-БД: после инцидента 2026-11-17 пуста — 0 проектов и 0 записей `code_audit`; прежние данные (33 проекта, 39 загрузок, 45 suite, 107 evidence) утрачены, см. [incident-2026-11-17.md](incident-2026-11-17.md).
- CI: `.github/workflows/tests.yml` — push/pull_request в `main`, PostgreSQL 17, pyflakes-страж на `undefined name`, pytest.

## Что проверено живьём (снимок на момент подготовки)

- Аудит демо-ZIP: **12 замечаний в 8 файлах** (1 CRITICAL, 7 HIGH, 1 MEDIUM, 3 LOW).
- Бинарный `.cfe` без BSL: `metadata_only` → аудит `no_bsl_modules`, `passed=false`.
- Журнал аудита кода: три конструкции → `SEC010`/`SEC011`/`SEC012`; md 1234 байта, csv 923 байта (шапка + 3 строки) скачиваются.
- Пагинация замечаний: скан на 6000 замечаний отдаёт карточку 534 байта без `issues` (`issues_url`, `issues_kept=5000`, `total=6000`), страница `offset=0&limit=100` — 100 элементов / 62 КБ; у загрузки карточка 322 байта, страницы те же; `offset=5000` → пустой `items` при `total=6000`, `offset=4999` → 1 элемент. UI: первые 100, «Показать ещё», счётчик «показано N из 5000 сохранённых (всего замечаний 6000)», за границу среза запросов нет.
- Удаление: `DELETE /api/v1/audit/scans/{id}?confirm={id}` — без/неверный confirm 409, неизвестный id 404, успех `{scan_id, deleted:true}`; `DELETE /api/v1/projects/{id}?confirm={id}` — каскад `{uploads 1, audit 2, projects 1}`, SQL-проверка сирот (загрузки без проекта, анализы без версий, suite без проекта) — 0.
- Настройки: `TP_AUDIT_MAX_ISSUES` (100..20000, `/health` отдаёт `audit_max_issues`), `TP_CODEX_TIMEOUT_SECONDS` (1..900) и URL-переменные (`TP_LLM_BASE_URL`, `TP_1C_ODATA_URL`, `TP_1C_WEB_URL`, `IMPACT_URL`, `INTELLIGENCE_URL`, `WEB_URL`, `DATABASE_URL`) валидируются при старте: `htp://x`, `localhost:8082`, `mysql://…` роняют сервис с текстом «нужен URL со схемой …».
- Приёмка убирает за собой: `deleted project <id8>` + `deleted scan <id8>`; после двух прогонов подряд `code_audit` и проекты не выросли (42/33). `--keep`/`-Keep` оставляет оба объекта.
- Стражи документации: правила ↔ `docs/rules.md`, маршруты OpenAPI ↔ `docs/api.md`, переменные окружения ↔ `.env.example` (падают при расхождении, включая проверку комментариев).
- `/health`: 200, `{status, executor_1c_env, projects_with_connection, audit_max_issues}`; `executor_1c_env=configured` только у `intelligence` (там заданы `TP_1C_*`).
- CI-прогон в чистом `python:3.12-slim`: 284 passed на тот момент (снимок на момент подготовки, до последующих тестов).

## Заблокировано и требует вашего действия

- `TP_LLM_API_KEY` в `.env` **пуст**: при инциденте тест записал тестовые `TP_LLM_*` в `.env`. Реальный ключ **не найден в проверенных локальных местах** (`.env`, `.env.example`, `git log/stash/reflog`, `%TEMP%`, `~/.omp`, `AppData`). Вернуть из секрет-хранилища или перевыпустить у провайдера; безвозвратную утрату утверждать нельзя — проверены только перечисленные места. Сейчас `provider=llm` отвечает **503** «Ключ нейросети не задан…», шаг ИИ в приёмке пропускается.
- Канал включится после возврата ключа в `.env` и `docker compose up -d --force-recreate intelligence`.

## Известные ограничения

- Демо-база публикует 0 EntitySet: чтение сущности даёт `HTTP 404` с пояснением; доступность проверяется кейсом `$metadata`.
- `.cfe`/`.cf` без модулей BSL — только метаданные, аудит не выполняется.
- CRUD/проведение документов на живой 1С не прогонялись: только навигация, OData-чтение и синтетический стенд.
- Защита от изменяющих шагов эвристична (маркеры подписей + `fill`); строгий режим — `strict_read_only`. Детали — [api.md](api.md).
- Нет TLS, rate-limit и ролей на чтение: любой аутентифицированный видит все проекты, отчёты и evidence. Чек-лист выноса — [security.md](security.md).
- Evidence хранится в PostgreSQL (`ti_ai_evidence`, BYTEA) внутри тома `testpilot_pgdata`; `docker compose down -v` уничтожает БД и скриншоты.
- Пароль подключения 1С лежит в `impact_projects.auth_password` открытым текстом.
- Коммит `a244b67` в истории нерабочий — не точка отката.
- Push не выполнялся; сколько коммитов впереди — `git rev-list --count origin/main..HEAD`.

## Как продолжить

- Локальная приёмка: `scripts/acceptance.ps1` (PowerShell 5.1+) или `scripts/acceptance.sh` (bash) — без флагов проверяет стек, `/health`, миграции, демо-ZIP, журнал, pytest и убирает свои проект и скан.
- Полный ИИ-путь после возврата ключа: `scripts/acceptance.sh --llm --live-1c` (или `-IncludeLLM -IncludeLive1C`).
- Если шаг даёт FAIL: смотреть `docs/acceptance.md` и текст шага, не подавлять проверку и не править ожидания под факт; чинить причину.
- Лимиты и форматы переменных (размер БД, таймауты, URL) — `.env.example`; невалидное значение останавливает сервис на старте.
- Контракты и коды: [api.md](api.md). Правила сканера: [rules.md](rules.md). Безопасность и дефолты: [security.md](security.md). Ручной чек-лист: [acceptance.md](acceptance.md).

## Рекомендуемые следующие шаги

1. Вернуть `TP_LLM_API_KEY`, перезапустить `intelligence` и прогнать приёмку с `--llm --live-1c`, чтобы восстановить живое подтверждение ИИ-пути.
2. Разобрать 33 проекта поштучно (имя/дата/загрузки/suite), удалять только подтверждённо мусорные через `DELETE /api/v1/projects/{id}?confirm={id}`; журнал сканов чистить `DELETE /api/v1/audit/scans/{id}?confirm={id}`.
3. Перед выносом наружу: TLS-терминатор, смена дефолтных паролей, закрытые `/docs` и `/openapi.json`, роли на чтение отчётов и evidence.
4. Шифрование или secret-store для пароля подключения 1С (`impact_projects.auth_password`).
5. CRUD-сценарий с `allow_mutations=true` — важный непокрытый сценарий: успешных живых прогонов записи/проведения на 1С не было (проверены навигация, OData-чтение и синтетический стенд).
