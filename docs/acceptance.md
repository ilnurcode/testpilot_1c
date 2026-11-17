# Чек-лист приёмки (10 минут)

После `docker compose up -d --build --wait`.

Приёмка вызывает compose только через предохранители `scripts/compose.sh`/`compose.ps1`: они отказывают (код 3) на `down -v`, `volume rm` и `volume prune` рабочего проекта; разрешены только `-p testpilot-ci`/`-p testpilot-restore`.
В `pyproject.toml` в `addopts` нет `-q`: приёмка парсит строку «N passed», а `-qq` (addopts + вызов) её подавляет — `-q` передаёт вызывающая сторона.

Флаги: `--llm`/`-IncludeLLM`, `--live-1c`/`-IncludeLive1C`, `--probe-login`/`-ProbeLogin`, `--cfe`, `--no-build`/`-NoBuild`, `--keep`/`-Keep`, `--help`/`-Help`; `--live-1c` и `--llm` независимы. Для `--live-1c` обязателен `TP_ACCEPTANCE_PROJECT_ID` — проект с настроенным подключением (`web_url`/`odata_url`/пользователь): без него или без `web_url` шаг даёт FAIL, а не SKIP. Живой прогон идёт на этом проекте, временный демо-проект приёмки остаётся только для аудита/скана и удаляется; проект из `TP_ACCEPTANCE_PROJECT_ID` не удаляется.

**Автоматический прогон:** `scripts/acceptance.ps1` (PowerShell 5.1+) или `scripts/acceptance.sh` (bash) — одна команда, PASS/FAIL/SKIP по шагам и код возврата 1 при любом FAIL. По умолчанию выполняются только локальные проверки: стек, `/health`, миграции, демо-ZIP, журнал аудита с отчётами, `pytest`. Внешние шаги включаются явно и независимо: `-IncludeLLM`/`--llm` — генерация плана у внешнего провайдера (пустой `TP_LLM_API_KEY` даёт FAIL с текстом про ключ), `-IncludeLive1C`/`--live-1c` — approve навигационного предложения и `run-1c` на базе 1С; без `--llm` план для живого прогона строится `provider=local`, ключ LLM не нужен. Без обоих флагов раздел даёт SKIP и сетевых вызовов не делает. Живой шаг запускает владелец: он обращается к реальной публикации. Фактическое число тестов печатает `scripts/acceptance.sh` и `pytest tests/ -q`; в документации числа не дублируются.

UI smoke: `tests/test_ui_smoke.py` (маркер `ui`) обходит `#overview`, `#projects`, `#audit`, `#ai` и карточку проекта в Chromium через Playwright и падает на console-ошибках, `pageerror`, тексте `undefined`/`NaN`/`[object Object]` и JS-падениях вида «is not a function». Запуск требует `TP_UI_BASE_URL` (в compose-скрипте — `http://web:8000`, для ручного прогона на host — `http://127.0.0.1:8000`) и `TP_EXPERT_PASSWORD`; в обычном прогоне и CI без этих переменных тест пропускается; в приёмке он обязателен — скрипты выставляют `TP_UI_REQUIRED=1`, и недоступный интерфейс (остановленный `web`, нет Chromium, страница не отвечает) даёт FAIL с текстом причины, а не SKIP. Приёмка выставляет обе переменные сама и печатает строку `UI smoke: N проверок OK (overview, projects, project, audit, ai)`.

Прочее: `-CfePath <file>`/`--cfe <file>` (или `CFE_PATH`; без файла шаг .cfe пропускается), `-NoBuild`/`--no-build`, `EXPECT_DEMO_ISSUES` (по умолчанию 12), `TP_EXPERT_PASSWORD` (иначе из `.env` или из окружения контейнера). Read-only зонд формы входа включается флагом `-ProbeLogin`/`--probe-login` и требует `TP_ACCEPTANCE_PROJECT_ID` (id проекта с настроенным `web_url`): зонд только открывает страницу и читает разметку, без ввода учётных данных, кликов и approve; без флага печатается `SKIP probe-login: флаг не передан`. Созданный прогоном проект удаляется в конце (в том числе при провале проверки) через `DELETE /api/v1/projects/{id}?confirm={id}`; `-Keep`/`--keep` оставляет его. Записи журнала аудита кода к проекту не привязаны и не удаляются — в сводке печатается их текущее число. Подробности: [README](../README.md), контракты API: [api.md](api.md), ограничения: [limitations.md](limitations.md), ручной живой прогон на DemoTrd: [live-run.md](live-run.md).
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

## То же в CI

В `.github/workflows/tests.yml` две джобы: `pytest` (юнит-тесты на PostgreSQL 17) и `acceptance`, которая после неё выполняет `docker compose up -d --build --wait` и `bash scripts/acceptance.sh --no-build` на собранном стеке. Шаг ИИ и живая 1С в CI остаются `SKIP` (нет `TP_LLM_API_KEY`, флаги не передаются), UI-smoke обязателен: скрипт выставляет `TP_UI_REQUIRED=1`, поэтому остановленный или недоступный `web` валит джобу. При падении логи контейнеров и `.runtime/*.log` выгружаются артефактом `acceptance-logs` (7 дней), стек всегда останавливается `docker compose down -v`.

## Быстрая офлайн-проверка (без БД и Docker)

```
bash scripts/smoke-offline.sh        # или: powershell -File scripts\smoke-offline.ps1
```

Запускает 19 файлов тестов, которым хватает локального venv: стражи конфигурации, документации, скриптов,
OpenAPI и шлюза, парсеры 1С-адаптеров. Ожидание: все тесты пройдены, UI-smoke — `skip` (нет `TP_UI_BASE_URL`).
Docker, compose и БД не используются; без локального venv скрипт завершается кодом 2 с подсказкой.

## Известные ограничения

- Демо-база публикует 0 EntitySet: чтение сущности даёт `HTTP 404`, в отчёте и UI появляется пояснение про публикацию; доступность сервиса проверяет кейс `$metadata`.
- `.cfe`/`.cf` без модулей BSL — только метаданные (`metadata_only`, `no_bsl_modules`), аудит не выполняется.
- Защита от изменяющих шагов эвристична (маркеры кнопок + любой `fill`): не распознаются «ОК», «Подтвердить», пользовательские подписи, клики по строкам и иконкам, ввод без отправки, автодействия платформы. Подробнее — [api.md](api.md).
- `strict_read_only=true` разрешает только `assert_text`, `assert_section`, `wait`, `open_url` — это единственный режим без побочных эффектов.
- `executor_1c_env` относится только к env-профилю `TP_1C_*`; прогоны используют настройки подключения проекта (`projects_with_connection`).
- Коммит `a244b67` в истории нерабочий — не использовать как точку отката.
- Ветка `main` опережает `origin/main` на 28 коммитов, `git push` не выполнялся.
