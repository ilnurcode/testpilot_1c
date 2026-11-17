# Чек-лист приёмки (10 минут)

После `docker compose up -d --build --wait`.

**Автоматический прогон:** `scripts/acceptance.ps1` (PowerShell 5.1+) или `scripts/acceptance.sh` (bash) — одна команда, PASS/FAIL/SKIP по шагам и код возврата 1 при любом FAIL. По умолчанию выполняются только локальные проверки: стек, `/health`, миграции, демо-ZIP, журнал аудита с отчётами, `pytest`. Внешние шаги включаются явно: `-IncludeLLM`/`--llm` — генерация плана у внешнего провайдера, `-IncludeLive1C`/`--live-1c` — прогон на базе 1С; без них эти шаги дают SKIP и сетевых вызовов не делают. Прочее: `-CfePath <file>`/`--cfe <file>` (или `CFE_PATH`; без файла шаг .cfe пропускается), `-NoBuild`/`--no-build`, `EXPECT_DEMO_ISSUES` (по умолчанию 12), `TP_EXPERT_PASSWORD` (иначе из `.env` или из окружения контейнера). Подробности: [README](../README.md), контракты API: [api.md](api.md), ограничения: [limitations.md](limitations.md).
Креды по умолчанию: `expert` / `$TP_EXPERT_PASSWORD` (`TestPilotLocal123!` в compose).

## Стек

| Что | Команда / путь | Ожидаемо |
|---|---|---|
| Сборка и запуск | `docker compose up -d --build --wait` | db, impact, intelligence, web — Healthy; migrate — Exited (0) |
| Ревизия БД | `docker compose exec -T intelligence alembic current` | `0007 (head)` |
| Health | `curl -s http://127.0.0.1:8000/health` | `{"status":"ok","executor_1c_env":"not_configured","projects_with_connection":<N>}` |
| Интерфейс | `http://127.0.0.1:8000` | рабочая область открыта, в подвале профиль роли |

## Аудит кода

| Что | Команда / путь | Ожидаемо |
|---|---|---|
| Демо-ZIP | UI: Проекты → загрузить `samples/demo_vulnerable_extension.zip` → «Аудит кода и стандарты v8std» | 12 замечаний в 8 файлах: 1 CRITICAL, 7 HIGH, 1 MEDIUM, 3 LOW |
| Бинарный контейнер | UI: загрузить `.cfe` без модулей BSL → «Аудит кода» | статус загрузки `metadata_only`, аудит `status=no_bsl_modules`, `passed=false` |
| Вставленный код | `curl -s -u expert:$TP_EXPERT_PASSWORD -H 'Content-Type: application/json' -d '{"code":"ОтключитьБезопасныйРежим(Истина);"}' http://127.0.0.1:8000/api/v1/audit/scan` | `total_issues>=1`, `issues[0].rule_id=SEC010`, в ответе есть `scan_id` |
| Без учётных данных | тот же запрос без `-u` | HTTP 401 |
| Три конструкции | `POST /api/v1/audit/scan` с кодом из `tests/test_code_audit_journal.py::CODE` | ровно 3 замечания: SEC010, SEC011, SEC012 |
| Журнал | `curl -s -u expert:$TP_EXPERT_PASSWORD 'http://127.0.0.1:8000/api/v1/audit/scans?limit=1'` | массив с `scan_id`, `total_issues`, `created_at` |
| Отчёт по скану | `.../api/v1/audit/scans/<scan_id>/report` и `?format=csv` | md/csv как `attachment`, `audit-<id>.md` / `.csv` |

Список правил с уровнями, условиями и честными границами — [rules.md](rules.md).

## План и прогон

| Что | Команда / путь | Ожидаемо |
|---|---|---|
| Генерация | UI: «Нейросеть и проверки» → «Подготовить проверки» (или `POST /api/v1/ai/suites` с `provider=local`) | план с предложениями Playwright/OData; у рискованных — `mutating: true` |
| Утверждение | UI: выбрать навигацию → «Подтвердить выбранные сценарии» | статус `approved`, в истории появляется запись |
| Прогон на DemoTrd | UI: «Запустить на базе 1С» | `counts.passed=1`, `evidence_id` есть, `web_url` = адрес, который ответил |
| Повтор | UI: «Прогнать снова» | новый `suite_id`, исходный suite не изменён, новый сверху в истории |
| История | UI: проект → «Прогоны проверок» | дата, модель, статус, passed/failed/error/skipped, колонка «Изменения» |
| Отчёт | карточка прогона → «Скачать отчёт о прогоне» | `run-<suite_id>.md` как `attachment`; csv — строка на каждый кейс |

Перед выносом стенда наружу (не localhost) обязательно прочитать [security.md](security.md): дефолтные пароли, отсутствие TLS, открытые `/docs` и `/openapi.json`, общий доступ ролей к отчётам и evidence.

## Известные ограничения

- Демо-база публикует 0 EntitySet: чтение сущности даёт `HTTP 404`, в отчёте и UI появляется пояснение про публикацию; доступность сервиса проверяет кейс `$metadata`.
- `.cfe`/`.cf` без модулей BSL — только метаданные (`metadata_only`, `no_bsl_modules`), аудит не выполняется.
- Защита от изменяющих шагов эвристична (маркеры кнопок + любой `fill`): не распознаются «ОК», «Подтвердить», пользовательские подписи, клики по строкам и иконкам, ввод без отправки, автодействия платформы. Подробнее — [api.md](api.md).
- `strict_read_only=true` разрешает только `assert_text`, `assert_section`, `wait`, `open_url` — это единственный режим без побочных эффектов.
- `executor_1c_env` относится только к env-профилю `TP_1C_*`; прогоны используют настройки подключения проекта (`projects_with_connection`).
- Коммит `a244b67` в истории нерабочий — не использовать как точку отката.
- Ветка `main` опережает `origin/main` на 28 коммитов, `git push` не выполнялся.
