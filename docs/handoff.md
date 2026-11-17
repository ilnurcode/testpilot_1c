# Передача проекта

Короткая сводка состояния. Подробности — в [api.md](api.md), [rules.md](rules.md), [security.md](security.md), [acceptance.md](acceptance.md).

## Состояние

- Коммит: `b4f55cb` (`docs: документ передачи состояния`), ветка `main`.
- Тесты: **291 passed, 1 skipped** (`docker compose exec -T intelligence python -m pytest tests/ -q`, 73 с).
- Миграции: `0007 (head)` (`docker compose exec -T intelligence alembic current`).
- Compose: `db`, `impact`, `intelligence`, `web` — Up (healthy); `migrate` — Exited (0).
- Dev-БД: 33 проекта, 39 загрузок, 45 suite (накоплено приёмками и ручными проверками).
- CI: `.github/workflows/tests.yml` — push/pull_request в `main`, PostgreSQL 17, pyflakes-страж на `undefined name`, pytest.

## Что проверено живьём

- Аудит демо-ZIP: **12 замечаний в 8 файлах** (1 CRITICAL, 7 HIGH, 1 MEDIUM, 3 LOW).
- Бинарный `.cfe`: загрузка `platform-empty.cfe` → `metadata_only`, аудит → `no_bsl_modules`, `passed=false` (по сохранённой записи БД).
- Журнал аудита кода: скан трёх конструкций → `SEC010`/`SEC011`/`SEC012`; отчёты md (1265 байт) и csv (955 байт) скачиваются.
- ИИ-план (пока ключ был живым): suite `8308a81d` → прогон навигации `passed=1`, evidence `9897ad87`; повтор `fade38f7` → `passed=1`, evidence `bf21c4bd`.
- Повтор прогона: rerun суита `84863807` → новый `f4cd01dd`, исходный не изменён.
- Отчёт о прогоне по кейсам: suite `62c44714` → csv 960 байт, 2 строки данных (`passed`, `failed`), md с `case 1`/`case 2`.
- Удаление проекта: каскад `{suites 1, packages 1, tests 1, runs 1, results 1, coverage 1, uploads 1, audit 6, projects 1}`, SQL-проверка сирот — 0 (кроме штампа `audit.delete_project`).
- `/health`: 200, `{"status":"ok","executor_1c_env":...,"projects_with_connection":...}`.
- CI-прогон в чистом `python:3.12-slim`: 284 passed на тот момент (до последних трёх тестов).

## Заблокировано и требует вашего действия

- `TP_LLM_API_KEY` в `.env` **пуст**: при инциденте тест записал тестовые `TP_LLM_*` в `.env`, реальный ключ утрачен. Сейчас `provider=llm` отвечает **503** «Ключ нейросети не задан…», а шаг ИИ в приёмке — SKIP.
- Канал включится после возврата ключа в `.env` (вручную, из вашего секрет-хранилища) и перезапуска стека: `docker compose up -d --force-recreate intelligence`.

## Известные ограничения

- Демо-база публикует 0 EntitySet: чтение сущности даёт `HTTP 404` с пояснением; доступность проверяется кейсом `$metadata`.
- `.cfe`/`.cf` без модулей BSL — только метаданные, аудит не выполняется.
- CRUD/проведение документов на живой 1С не прогонялись: проверялись только навигация, OData-чтение и синтетический стенд.
- Защита от изменяющих шагов эвристична (маркеры подписей + `fill`); «ОК», «Подтвердить», пользовательские подписи, клики по строкам и автодействия не распознаются. Строгий режим — только `strict_read_only`.
- Нет TLS, rate-limit и ролей на чтение: любой аутентифицированный видит все проекты, отчёты и evidence.
- Пароль подключения к 1С лежит в `impact_projects.auth_password` открытым текстом.
- Коммит `a244b67` в истории нерабочий — не точка отката.
- Push не выполнялся: `main` впереди `origin/main` на **46 коммитов**.

## Как продолжить

- Локальная приёмка: `scripts/acceptance.ps1` (PowerShell 5.1+) или `scripts/acceptance.sh` (bash) — без флагов проверяет стек, `/health`, миграции, демо-ZIP, журнал и pytest.
- Внешние шаги — только явно: `--llm`/`-IncludeLLM` (провайдер) и `--live-1c`/`-IncludeLive1C` (прогон на 1С); после возврата ключа.
- Контракты и коды: [api.md](api.md). Правила сканера: [rules.md](rules.md). Безопасность и дефолты: [security.md](security.md). Ручной чек-лист: [acceptance.md](acceptance.md).

## Рекомендуемые следующие шаги

1. Вернуть `TP_LLM_API_KEY`, перезапустить `intelligence` и прогнать приёмку с `--llm --live-1c`, чтобы восстановить живое подтверждение ИИ-пути.
2. Решить судьбу 33 тестовых проектов в dev-БД: удалять только мусорные через `DELETE /api/v1/projects/{id}?confirm={id}`, приёмочные оставить.
3. Перед выносом наружу: TLS-терминатор, смена дефолтных паролей, закрытые `/docs` и `/openapi.json`, роли на чтение отчётов и evidence.
4. Шифрование или secret-store для пароля подключения 1С (`impact_projects.auth_password`).
5. Ротация паролей подключения 1С после любого дампа БД или бэкапа `pgdata`.
