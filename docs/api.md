# API и контракты 1.0

Все бизнес-запросы требуют Basic Auth; `/health`, `/docs`, `/openapi.json` доступны без аутентификации. `GET /health` отдаёт `{"status":"ok","executor_1c_env":"configured|not_configured","projects_with_connection":<N>}`: первое поле — только наличие `TP_1C_ODATA_URL`/`TP_1C_WEB_URL` в окружении сервиса, второе — число проектов в БД с заполненным `web_url` или `odata_url`. Прогоны на базе 1С используют настройки проекта, а не env-профиль, поэтому `not_configured` не означает, что прогоны недоступны. 503 — миграции не применены. Ошибки: `{"error":{"code":422,"message":"…","fields":[…]}}`; поля подробностей могут быть списком в `message`. Коды 401/403 — доступ, 404 — отсутствующая запись, 409 — конфликт, 413 — размер, 422 — схема, 502/503 — доставка. Успешные операции JSON обычно возвращают 200.

## Impact (8001)

| Метод | Путь `/api/v1/…` | Назначение |
|---|---|---|
| POST | imports | Тело Configuration 1.0, транзакционная версия |
| GET | versions | Список версий и контрольных сумм |
| POST | compare | configuration_id, base_version, target_version; сохраняет diff |
| POST | analyses | Те же поля + max_depth (0–100), edge_types, direction |
| GET | analyses | История сравнений и анализов |
| GET | analyses/{id} | Параметры, diff и пакет |
| GET | analyses/{id}/export | ImpactPackage 1.0 |
| POST | analyses/{id}/deliver | Повторяемая HTTP-доставка по INTELLIGENCE_URL |
| GET | graph | configuration_id, version_name, limit≤500, focus, depth≤10, node_type, edge_type, direction=incoming/outgoing/both |
| GET | audit | Последние 200 действий |
| POST / GET | projects | Создание / список: `q` (подстрока имени, без учёта регистра), `only_with_issues=true` (последний прогон с `failed>0` или `error>0`), `limit` 1..200 (по умолчанию 50), `offset≥0`; ответ `{items, total, limit, offset}`, в каждом элементе `last_run` (`suite_id`, `status`, `counts`, `finished_at`, `created_at`) или `null` |
| GET / DELETE | projects/{project_id} | Карточка с загрузками и анализами / удаление одной транзакцией (`?confirm=<project_id>`, без него 409) |
| POST | projects/{project_id}/uploads | Тело файла + `filename`, `kind`, `component`, `label`, `baseline_id` |
| GET | projects/{project_id}/uploads/{upload_id}/download | Исходные байты загрузки |
| POST | projects/{project_id}/uploads/{upload_id}/analyze | Анализ загрузки относительно baseline |
| PUT | projects/{project_id}/connection | `odata_url`, `web_url`, учётные данные 1С |
| POST | projects/{project_id}/connection/probe | Read-only проверка публикации OData и веб-клиента (отчёт содержит фактический адрес) |
| GET | projects/{project_id}/connection/enable-odata-script | Скрипт включения OData для 1С |

Объекты имеют явный object_id, type, full_name, parent_id, attributes. Родитель валидируется; принадлежность в графе задавайте явным ребром `belongs_to`. Циклы вызовов разрешены; цикл parent_id запрещён. Связи только между существующими узлами. Для внешних/динамических ссылок используйте `external`/`dynamic` между известными сущностями, с соответствующим происхождением; неполученные внешние объекты не выдумываются автоматически.

Импорт сохраняет SHA-256 сырых байтов и семантический SHA-256 содержимого без imported_at, с отсортированными объектами/рёбрами. Идентичное содержимое с перестановкой JSON-полей идемпотентно. Другие данные под прежним номером — 409. Схема собственного учебного формата, а не XML выгрузки 1С.

## Intelligence (8002)

| Метод | Путь `/api/v1/…` | Назначение |
|---|---|---|
| POST / GET | impact-packages | Проверка и хранение зоны / список |
| GET / POST | tests | Каталог / создание |
| GET / PUT / DELETE | tests/{id} | Чтение / полная карточка / архивирование |
| PUT | tests/{id}/coverage | object_id, state, source, reason |
| GET | coverage | Связи и экспертные решения |
| POST / GET | history | Импорт JSON RunInput / история |
| GET | defects | Дефекты с происхождением |
| GET / PUT | settings/weights | Централизованные нормированные веса |
| POST | ml/train | configuration_id, synthetic; обучение и допуск |
| GET | ml/models | Версии моделей и метрики |
| POST | plans | package_id, budget_seconds, min_coverage (0–1), use_ml |
| GET | plans / plans/{id} | История / полный объяснимый план |
| POST | plans/{id}/approve | reason, selected_ids или исходный набор, allow_deviation |
| GET | plans/{id}/export?format=json/csv | Полный JSON / CSV рейтинга |
| POST | plans/{id}/execution-jobs | Подтверждённый план → задание; ничего не запускает |
| GET | execution-jobs/{id} | Статус и состав задания |
| POST | execution-results | execution_job_id и run; полный внешний отчёт |
| GET | audit | Последние 300 действий; только expert |

Снятие mandatory/понижение critical: PUT tests/{id}?reason=обоснование, роль expert. critical при создании автоматически mandatory. Для статусов coverage confirmed/rejected нужен expert; unconfirmed доступен analyst. PUT карточки требует полный TestCase; перенос между конфигурациями/происхождениями запрещён. Совместимость — явный список допустимых строк версий; пустой список означает все версии конфигурации (не semver-диапазон).

История: run_id, configuration_id, version, synthetic, results[], defects[]. Только `passed/failed/error/skipped`, даты с timezone, duration_seconds≥0. Повторный идентичный run_id идемпотентен; иной отчёт — 409. Дефекты immutable и относятся к тестам этого прогона. `synthetic` истории обязан соответствовать каталогу.

## AI-план и прогон (Intelligence 8002)

| Метод | Путь `/api/v1/…` | Назначение |
|---|---|---|
| POST / GET | ai/suites | Генерация плана (`provider`, для внешних — `consent`) / история прогонов проекта (`?project_id=…&limit=`) |
| GET | ai/suites/{id} | Полный план; у каждого предложения поле `mutating` |
| POST | ai/suites/{id}/approve | Подтверждение выбранных предложений (expert) |
| POST | ai/suites/{id}/run-1c | Прогон на базе 1С по настройкам проекта; флаги `allow_mutations`, `strict_read_only` |
| POST | ai/suites/{id}/rerun | Повтор сохранённого плана новой записью suite (исходная не меняется) |
| GET | ai/suites/{id}/report?format=md/csv | Отчёт о прогоне: md по кейсам, csv — одна строка на результат |
| POST | ai/suites/{id}/run-demo | Прогон на синтетическом стенде presentation-sales |
| GET | ai/status, ai/settings/llm, ai/codex/status | Состояние провайдеров и Codex |
| PUT | ai/settings/llm | `base_url`/`model`/`api_key` — только в памяти процесса; `persist=true` требует `TP_ALLOW_ENV_WRITE=1` и пишет в файл `TP_ENV_FILE` (по умолчанию `.env`) |
| POST / GET | ai/codex/login, ai/codex/logout | Вход и выход Codex (expert) |
| GET | ai/evidence/{id} | PNG-скриншот прогона (хранится в БД, таблица `ti_ai_evidence`, колонка BYTEA; отдаётся с `X-Content-Type-Options: nosniff`) |

`PUT /api/v1/ai/settings/llm` отвечает `persisted`, `env_file`, `env_write_allowed` и `note`: по умолчанию настройки меняются только в памяти процесса (`persisted: false`), `api_key` никогда не возвращается (только `has_key`). Запись в файл окружения включается `TP_ALLOW_ENV_WRITE=1`, путь берётся из `TP_ENV_FILE`; попытка `persist=true` без флага — 409 «Запись в .env отключена…».

История отдаёт сводку без `suggestion`/`payload`: `suite_id`, `package_id`, `model`, `provider`, `status`, `counts`, `created_at`, `finished_at`, `approved_count`, `mutating_count`.
Отчёты отдаются вложением `run-<suite_id>.md|csv` и строятся только из сохранённых данных; для прогона без результатов вместо строк кейсов стоит пометка «результата нет».

## Checksum и обмен

`ImpactPackage` содержит schema_version, package_id, configuration_id, base_version, target_version, analysis_id, created_at, synthetic, changed_objects, affected_objects, dependency_paths, warnings, checksum. JSON Schema в `contracts`, строгие проверки ссылок и checksum в Pydantic.

Контрольная сумма — SHA-256 UTF-8 JSON после валидации модели, без поля checksum; `ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')`, `allow_nan=False`. Даты сериализуются Pydantic ISO 8601 (UTC — Z). Списки экспортного пакета уже детерминированно упорядочены алгоритмом, потребитель их не переставляет. Эталонная реализация — `testpilot.contracts.checksum`. Это контроль целостности, не цифровая подпись.

Повтор одного package_id с тем же checksum не дублируется. Другой checksum того же ID — 409. Сохранённый анализ доступен при сетевой ошибке отправки; повторяется только `/deliver`.

`contracts/execution-job-1.0.schema.json` и `execution-report-1.0.schema.json` описывают будущий runner. Артефакты — только строки HTTPS, сервис их не скачивает. Не передавайте секреты внутри executor_parameters: используйте отдельный защищённый канал будущего исполнителя.


## Проекты и файлы

Список проектов отдаётся страницей: на страницу уходит один запрос за `items` и один `count` за `total` (два SQL, без N+1); сортировка — `created_at DESC, id DESC`, поэтому страницы с `offset` не пересекаются. `limit` вне 1..200 и отрицательный `offset` — 400.

Проект удаляется через `DELETE /api/v1/projects/{id}?confirm=<id>` (роль expert, без `confirm` — 409): одной транзакцией уходят загрузки, анализы, `suite` с их evidence, тесты и прогоны утверждённых планов, неразделяемые пакеты и записи `audit` по этим идентификаторам; совместные пакеты и тесты, планы, задания исполнения, модели ML, справочники конфигурации и журнал `code_audit` остаются. Ответ содержит `deleted` с числом удалённых записей по типам.

Добавлены API библиотеки проектов и оригинальных загрузок. Полное описание параметров, режимов и ограничений — [uploads.md](uploads.md). Тело upload передаётся исходными байтами, а не multipart. Обычные JSON API сохраняют лимит 8 МиБ; файловая загрузка ограничена 64 МиБ.

## Журнал аудитов вставленного кода

| Метод | Путь `/api/v1/audit/…` | Назначение |
|---|---|---|
| POST | scan | `code` или `path`; выполняет аудит и сохраняет запись, возвращает карточку с `scan_id` |
| GET | scans?limit=1..100 | Последние сохранённые аудиты (по умолчанию 20), сортировка `created_at DESC, id DESC` |
| GET | scans/{scan_id} | Карточка записи в той же форме, что ответ `POST /scan`; 404 без записи |
| GET | scans/{scan_id}/issues?offset=0&limit=100 | Постраничная выдача замечаний: `limit` 1..1000, `offset` ≥ 0 (иначе 400), 404 без записи |
| GET | scans/{scan_id}/report?format=md/csv | Отчёт по записи; 400 на другой формат, 404 без записи |
| POST | scan-upload | Аудит файла или архива, загруженного в проект |
| GET | uploads/{upload_id}/report?format=md/csv | Отчёт по сохранённому аудиту загрузки |
| GET | uploads/{upload_id}/issues?offset=0&limit=100 | Постраничная выдача замечаний аудита загрузки: те же правила и порядок, что у сканов |

Карточка записи: `scan_id`, `source_name`, `sha256`, `created_at`, `status`, `message`, `total_files`, `sources_checked`, `total_issues`, `issues_by_severity`, `issues_by_category`, `issues_url`, `issues_kept`, `issues_truncated`, `issues`, `passed`, `duration_ms`. `GET /scans/{scan_id}` отдаёт её без изменений относительно ответа `POST /scan`.

Совместимость: если замечаний не больше `MAX_ISSUES_KEPT` (5000), поле `issues` в карточке содержит их все, как раньше. Если больше — детали в карточке не отдаются (ключа `issues` нет), вместо них `issues_url` на постраничную выдачу; `total_issues` и распределение по уровням всегда считаются по полному числу.

`GET /scans/{scan_id}/issues` и `GET /uploads/{upload_id}/issues` отдают `{items, total, offset, limit, issues_truncated, issues_kept}`, где `total` — полное число замечаний (`total_issues`), а `items` — сохранённый срез. `limit` — 1..1000, `offset` ≥ 0 (иначе 400), неизвестная запись без аудита — 404. Порядок стабильный: файл, строка, колонка, затем порядок сохранения; он не зависит от порядка обхода каталога при сканировании. Запрос за границей среза (`offset >= issues_kept`) возвращает 200 с пустым `items` и полным `total`.

Карточка сохранённого аудита загрузки (`GET /uploads/{upload_id}`) подчиняется тому же правилу, что и карточка скана: до 5000 замечаний `issues` в ответе есть, больше — только `issues_url`, `issues_kept` и `total_issues`.

Детализация ограничена: `MAX_ISSUES_KEPT = 5000` (см. `testpilot/scanner/engine.py`). Счётчики `total_issues`, `issues_by_severity` и `issues_by_category` считаются по всем замечаниям, а в записи и ответе хранятся только первые 5000 — `issues_kept` показывает, сколько деталей сохранено, `issues_truncated` — сработал ли срез. Это же ограничение действует для `POST /scan-upload` и сохранённого `audit_result` загрузки.

Доступ: `POST /scan` и `POST /scan-upload` требуют Basic Auth, потому что пишут запись в БД. Чтение — `GET /scans`, `GET /scans/{id}` и оба `/report` — ничего не записывает. Без учётных данных или с неверным паролем ответ 401; 403 отдают маршруты с ограничением роли (на этих маршрутах роль не ограничена, достаточно любой аутентифицированной). Отчёты отдаются как `attachment` с `Content-Disposition: attachment; filename="audit-<id>.md|csv"`.

`sha256` в карточке — отпечаток проверенного входа: текст вставленного кода либо манифест файлов каталога (упорядоченные по относительному пути пары «относительный путь + sha256 содержимого файла», свернутые в один sha256). Путь каталога и его mtime в отпечаток не входят.

Полный исходник не сохраняется; сохраняются отпечаток, сводка и фрагменты найденных строк (до 140 символов, обрезка при сохранении, а не при отображении). Отчёты загрузок и журнала сканов строит один рендер `testpilot.web.audit_report.render_audit_report`: md и csv с колонками «Правило; Уровень; Файл:строка; Сообщение; Фрагмент кода».

Если детализация была урезана, md-отчёт печатает строку «показаны первые N из M замечаний (детализация ограничена)». CSV остаётся строго табличным (шапка + сохранённые строки), поэтому признак среза виден в заголовках ответа `X-Audit-Issues-Kept`, `X-Audit-Issues-Total`, `X-Audit-Issues-Truncated`.

## Режимы прогона плана на базе 1С

`POST /api/v1/ai/suites/{id}/run-1c` и `POST /api/v1/ai/suites/{id}/rerun` принимают необязательные флаги; по умолчанию действует эвристическая защита по тексту кнопки.

| Режим | Параметры | Что разрешено | Поведение |
|---|---|---|---|
| По умолчанию | — | шаги без признаков изменения | 422, если в утверждённых предложениях есть `click` по кнопкам «Записать», «Провести», «Создать», «Удалить», «Отменить проведение», «Пометить на удаление», «Скопировать», «Изменить» или любой `fill`; в сообщении перечислены предложение, шаг и подпись кнопки |
| Изменения разрешены | `allow_mutations=true` | любые шаги плана | выполняется как есть; согласие сохраняется в результате (`allow_mutations: true`) |
| Строгий read-only | `strict_read_only=true` | только `assert_text`, `assert_section`, `wait`, `open_url` (origin проверяется исполнителем) | любой `click`/`fill` → 422 с указанием предложения, шага и действия; `allow_mutations` в этом режиме не действует; результат содержит `strict_read_only: true` |

Эвристика по умолчанию опирается на текст кнопки и неполна. Она **не** блокирует: кнопки с другими подписями («ОК», «Подтвердить», «Да», «Готово», пользовательские), клики по строкам, ячейкам и иконкам без текста-маркера, ввод в поля, не отправляющие данные (локальная фильтрация), автоматические действия платформы (автозаполнение, пересчёт), а также любые сценарии, которые меняют данные без клика по кнопке. Полное отсутствие побочных эффектов гарантирует только `strict_read_only=true`.

Пометка `mutating` у предложения плана (`GET /api/v1/ai/suites/{id}` и ответ `POST /api/v1/ai/suites`) считается той же эвристикой и только информирует: подтверждение плана (`approve`) она не блокирует. Сводка истории (`GET /api/v1/ai/suites`) содержит `mutating_count` — число предложений плана с потенциально изменяющими шагами.
