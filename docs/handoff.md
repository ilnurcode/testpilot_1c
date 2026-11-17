# Передача проекта

Короткая сводка состояния. Подробности — в [api.md](api.md), [rules.md](rules.md), [security.md](security.md), [acceptance.md](acceptance.md). Человекочитаемый список изменений по темам — [CHANGELOG.md](../CHANGELOG.md).

## Состояние БД после инцидента

Данные до инцидента **не восстановлены**: 33 проекта, 39 загрузок, 45 suite, 42 записи `code_audit` и 107 evidence утрачены вместе с томом `testpilot_pgdata` (плюс `testpilot_codex_auth`). Причина, поиск бэкапов и выводы — [incident-2026-11-17.md](incident-2026-11-17.md); пересоздание данных по шагам — [recreate-demo.md](recreate-demo.md). Репозиторий и тестовые ассеты не пострадали.

Текущая БД (снимок после приёмки t101) наполнена двумя демо-проектами — см. ниже.

## Состояние

- Коммит кода: `7515430` (последний перед этим документом), ветка `main`; точный HEAD — `git log -1 --oneline`.
- Тесты: число печатает `docker compose exec -T intelligence python -m pytest tests/ -q` и `scripts/acceptance.sh`; в документе не фиксируется.
- Миграции: `0008 (head)` (`docker compose exec -T intelligence alembic current`).
- Compose: стек поднят из образов текущего HEAD (`db`, `impact`, `intelligence`, `web` — Up (healthy), `migrate` — Exited (0)).
- Dev-БД: `7f87c09d` «Демо 2026-11-17 · аудит» — 1 загрузка `demo_vulnerable_extension.zip`, аудит 12 замечаний (1/7/1/3, 8 файлов), analyze 4 объекта, план `08ea3e37` в статусе `draft`, md/csv отчёты по загрузке; подключение не заполнено.
- Dev-БД: `d6f2fe6f` «Демо 2026-11-17 · аудит и проверки» — те же аудит/анализ плюс `web_url`/`odata_url` на `localhost:8088/DemoTrd` и пользователь `Администратор` (пароль пустой); прогон `2474f655`: 0 passed / 1 failed (вход в 1С не выполнен), evidence `7a4bd9f7`; успешный прогон `7df3e0be`: 1 passed, evidence `7cc60e71`.
- Журнал `code_audit` пуст (приёмка удаляет свои сканы).
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

### Локальные доказательства без реальной 1С

- Вход и навигация на локальной имитации с подтверждёнными id: `passed`, форма исчезает, `assert_section` находит «Продажи»; при неработающей кнопке — `RuntimeError` < 15 с (`tests/test_login_end_to_end_local.py`).
- Режимы прогона с реальными счётчиками: без `allow_mutations` → 422 и 0 POST; с `allow_mutations=true` → 1 POST `/saved`; `strict_read_only` + click → 422 и 0 POST; `strict_read_only` + `assert_text` → 0 POST (`tests/test_run_modes_local.py`).
- Зонд через API на локальной странице: 200, факты формы, `credentials_sent=false`, сервер видел только `GET /` (`probe-login`, boot=false и boot=true).
- `--live-1c` в приёмке проверен end-to-end на локальной имитации публикации: PASS, `passed=1`, evidence 8 073 байта, 4.57 с. Проект из `TP_ACCEPTANCE_PROJECT_ID` не удаляется, но получает новый suite, evidence (PNG) и сдвинутый `last_run`; в этой проверке им был наш временный мок-проект, проект владельца не затрагивался. Временный демо-проект приёмки удаляется.
- Подтверждено снимком: 1С после клика показывает состояние обработки (спиннер). Гипотеза — короткий grace; исправление (grace 30 с, распознавание `processing`) не перепроверено живьём.
- **Живой прогон на DemoTrd (2026-11-17) выполнен**: suite `7df3e0be-0b35-461a-9ae5-0e00fa64c0c3`, `status=completed`, `counts={passed:1, failed:0, error:0, skipped:0}`, `login={stage:after_click, user_filled:true, password_filled:false, clicked:true, form_visible_after:false}`, evidence `7cc60e71-8e54-48ff-b723-850f06e11682` (141 767 байт — рабочий стол 1С под «Администратором»), 98.26 с, `allow_mutations=false`, `strict_read_only=false`. Это единственный успешный живой прогон, и он read-only: вход + навигация в «Продажи», без изменения данных. Ранбук — [live-run.md](live-run.md).

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
2. Разобрать два демо-проекта (`7f87c09d`, `d6f2fe6f`) поштучно; удалять только подтверждённо мусорные через `DELETE /api/v1/projects/{id}?confirm={id}`, журнал сканов — `DELETE /api/v1/audit/scans/{id}?confirm={id}`.
3. Перед выносом наружу: TLS-терминатор, смена дефолтных паролей, закрытые `/docs` и `/openapi.json`, роли на чтение отчётов и evidence.
4. Шифрование или secret-store для пароля подключения 1С (`impact_projects.auth_password`).
5. CRUD-сценарий с `allow_mutations=true` — важный непокрытый сценарий: успешных живых прогонов записи/проведения на 1С не было (проверены навигация, OData-чтение и синтетический стенд).
