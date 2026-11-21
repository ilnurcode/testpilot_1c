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
| GET | ai/suites/{id}/preflight | Read-only preflight плана: `would_run`, `approved_indices`, `engines` (engine, mutating, steps_count, blocked_reason), `settings` (web_url/odata_url/пользователь/пароль), `runtime` (grace/login_wait/login_submit/audit_max_issues/предохранитель/режимы), `warnings`; браузер не запускается, в БД не пишется |
| DELETE | ai/suites/{id}?confirm={id} | Адресное удаление прогона (expert): без/неверный `confirm` — 409, неизвестный id — 404; каскад `evidence`, тестов `ai-<id>-*` с `coverage`/`priorities`, прогона `ai-run-<id>` с `results`/`defects` и самой строки suite; пакет и планы не удаляются; ответ `{suite_id, deleted:{…}}` |
| POST | ai/suites/{id}/approve | Подтверждение выбранных предложений (expert) |
| POST | ai/suites/{id}/run-1c | HTTP 202: постановка в очередь, ответ RunState (не полный suite); флаги `allow_mutations`, `strict_read_only` |
| GET | ai/suites/{id}/status | Лёгкий RunState для polling, без results/evidence |
| POST | ai/suites/{id}/cancel | Очередь: `cancelled`; выполняющийся прогон: `cancel_requested=true` до остановки; terminal: 409 |
| POST | ai/suites/{id}/rerun | HTTP 202: новая запись suite в очереди, новый `suite_id`; исходная история не меняется |
| GET | ai/suites/{id}/report?format=md/csv | Сохранённые кейсы и typed assertions; каждый результат и каждый отсутствующий кейс утверждённого плана представлены отдельно |
| POST | ai/suites/{id}/run-demo | Прогон на синтетическом стенде presentation-sales; принимает только план `provider=demo` — у утверждённых автоматических кейсов должны быть `quantity/price/discount/expected_total`, иначе 409 с причиной (typed-план исполняется в 1С через run-1c) |
| GET | ai/status, ai/settings/llm, ai/codex/status | Состояние провайдеров и Codex |
| PUT | ai/settings/llm | `base_url`/`model`/`api_key` — только в памяти процесса; `persist=true` требует `TP_ALLOW_ENV_WRITE=1` и пишет в файл `TP_ENV_FILE` (по умолчанию `.env`) |
| POST / GET | ai/codex/login, ai/codex/logout | Вход и выход Codex (expert) |
| GET | ai/evidence/{id} | PNG-скриншот прогона (хранится в БД, таблица `ti_ai_evidence`, колонка BYTEA; отдаётся с `X-Content-Type-Options: nosniff`) |
| POST | ai/connection/probe-login | Read-only зонд формы входа: адрес берётся только из сохранённого `web_url` проекта (в контейнере loopback переписывается на `host.docker.internal`), без ввода, кликов и fill. Тело: `project_id`, необязательный `boot` (bool, по умолчанию false): при `boot=true` same-origin non-GET разрешены, чтобы 1С отрисовала форму, чужой origin блокируется до отправки; учётные данные не отправляются (`credentials_sent=false`). Ответ: `mode` (`get`/`boot`), `fields`, `buttons`, `containers`, `frame_url`, `web_url_used`, `non_get_requests`, `blocked_requests`, `blocked_non_get`. 404 без проекта, 422 без `web_url` или при `boot` не bool, 503 при ошибке открытия/origin/отсутствии формы (с диагностикой фреймов) |

`PUT /api/v1/ai/settings/llm` отвечает `persisted`, `env_file`, `env_write_allowed` и `note`: по умолчанию настройки меняются только в памяти процесса (`persisted: false`), `api_key` никогда не возвращается (только `has_key`). Запись в файл окружения включается `TP_ALLOW_ENV_WRITE=1`, путь берётся из `TP_ENV_FILE`; попытка `persist=true` без флага — 409 «Запись в .env отключена…».

История отдаёт сводку без `suggestion`/`payload`: `suite_id`, `package_id`, `model`, `provider`, `status`, `counts`, `created_at`, `finished_at`, `approved_count`, `mutating_count`.
Отчёты отдаются вложением `run-<suite_id>.md|csv` и строятся только из сохранённых данных; для прогона без результатов вместо строк кейсов стоит пометка «результата нет».

## Веб-сервис: UI, шлюз и стенд (8000)

Всё под Basic Auth, кроме `/health`, `/static/*`, `/` (страница логина) и перечисленного отдельно.

| Метод | Путь | Назначение и коды |
|---|---|---|
| GET | / | HTML-оболочка UI; без учётных данных — 401 (форма логина) |
| GET | /api/whoami | Текущая роль из Basic Auth |
| GET / POST / PUT / DELETE | /gateway/{service}/{path} | Прокси к impact/intelligence с теми же заголовками; коды и тело — от целевого сервиса |
| POST | /api/demo | Демонстрационный прогон impact+intelligence (expert); ошибки контура — 502 |
| POST | /api/presentation | Презентационный набор данных |
| GET | /api/presentation/sample/{which} | Образец страницы стенда |
| POST | /api/sandbox/orders | Учебный стенд «Продажи»: создание заказа |
| GET / DELETE | /api/sandbox/orders/{oid} | Заказ стенда: чтение и удаление |
| GET | /api/sandbox/orders/{oid} | Заказ стенда |
| GET | /sandbox/sales | HTML страницы стенда |

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
| DELETE | scans/{scan_id}?confirm={scan_id} | Адресное удаление одной записи журнала: без/неверный `confirm` — 409, неизвестный id — 404, успех — `{scan_id, deleted: true}`; роль expert |
| GET | scans/{scan_id}/report?format=md/csv | Отчёт по записи; 400 на другой формат, 404 без записи |
| POST | scan-upload | Аудит файла или архива, загруженного в проект |
| GET | uploads/{upload_id} | Карточка сохранённого аудита загрузки (при >5000 замечаний без `issues`, с `issues_url`) |
| GET | uploads/{upload_id}/report?format=md/csv | Отчёт по сохранённому аудиту загрузки |
| GET | uploads/{upload_id}/issues?offset=0&limit=100 | Постраничная выдача замечаний аудита загрузки |
| GET | rules | Перечень правил сканера (без данных); единственный маршрут аудита без Basic Auth |
| GET | uploads/{upload_id}/issues?offset=0&limit=100 | Постраничная выдача замечаний аудита загрузки: те же правила и порядок, что у сканов |

Карточка записи: `scan_id`, `source_name`, `sha256`, `created_at`, `status`, `message`, `total_files`, `sources_checked`, `total_issues`, `issues_by_severity`, `issues_by_category`, `issues_url`, `issues_kept`, `issues_truncated`, `issues`, `passed`, `duration_ms`. `GET /scans/{scan_id}` отдаёт её без изменений относительно ответа `POST /scan`.

Совместимость: если замечаний не больше `TP_AUDIT_MAX_ISSUES` (по умолчанию 5000), поле `issues` в карточке содержит их все, как раньше. Если больше — детали в карточке не отдаются (ключа `issues` нет), вместо них `issues_url` на постраничную выдачу; `total_issues` и распределение по уровням всегда считаются по полному числу.

`GET /scans/{scan_id}/issues` и `GET /uploads/{upload_id}/issues` отдают `{items, total, offset, limit, issues_truncated, issues_kept}`, где `total` — полное число замечаний (`total_issues`), а `items` — сохранённый срез. `limit` — 1..1000, `offset` ≥ 0 (иначе 400), неизвестная запись без аудита — 404. Порядок стабильный: файл, строка, колонка, затем порядок сохранения; он не зависит от порядка обхода каталога при сканировании. Запрос за границей среза (`offset >= issues_kept`) возвращает 200 с пустым `items` и полным `total`.

Карточка сохранённого аудита загрузки (`GET /uploads/{upload_id}`) подчиняется тому же правилу, что и карточка скана: до 5000 замечаний `issues` в ответе есть, больше — только `issues_url`, `issues_kept` и `total_issues`.

Детализация ограничена: `MAX_ISSUES_KEPT = 5000` (см. `testpilot/scanner/engine.py`). Счётчики `total_issues`, `issues_by_severity` и `issues_by_category` считаются по всем замечаниям, а в записи и ответе хранятся только первые 5000 — `issues_kept` показывает, сколько деталей сохранено, `issues_truncated` — сработал ли срез. Это же ограничение действует для `POST /scan-upload` и сохранённого `audit_result` загрузки.

Настройки Codex (включая `TP_CODEX_TIMEOUT_SECONDS`, 1..900) и лимит детализации аудита валидируются при старте сервиса: невалидное значение не запускает процесс.

`GET /health` отдаёт `audit_max_issues` — фактический предел детализации, с которым запущен сервис (значение `TP_AUDIT_MAX_ISSUES`, 100..20000, по умолчанию 5000).

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

### Очередь и отображение результата (C1)

`RunState`: `suite_id`, `status`, `attempt_id`, `attempt_version`, `lease_expires_at`,
`cancel_requested`, `started_at`, `finished_at`, `plan_revision`,
`counts: {passed, failed, error, skipped}`, `business_success: bool|null`, `message`.
Поля `total` нет. Статусы очереди — `queued` / `running`; terminal — `completed`,
`error`, `cancelled`, `interrupted`. `completed` означает окончание выполнения,
а не успешность всех бизнес-требований.

UI читает полный план через `GET ai/suites/{id}`, после HTTP 202 начинает polling
`/status` и получает сохранённые results/evidence через полный GET на terminal.
Открытие ссылки `#ai/{id}` и reload возобновляют наблюдение за queued/running.
Переход на другой план/страницу, закрытие страницы, terminal и отказ polling
(включая 401) останавливают старый таймер; ошибка связи видна как неизвестное
состояние, не как завершение. Кнопка «Обновить состояние» повторно читает suite.
Отмена не откатывает уже выполненные действия в 1С.

Кейс содержит `assertions: [{assertion_id, scope, status, expected, actual, message}]`,
где `scope` — `business|connectivity|metadata|manual`, значения expected/actual —
типизированные `{type,value}` либо `null`. UI и отчёт сохраняют это различие:
connectivity/metadata passed не подтверждают business, manual skipped не считается
выполненной бизнес-проверкой. `business_success=null` или отсутствие этого поля
в полном GET suite показывается как «не проверен / не доказан», а не как ошибка
или бизнес-успех. Поля evidence provenance, причина отсутствия снимка и сохранённый
источник ошибки не подменяются утверждением о наличии evidence.

CSV сохраняет первые восемь колонок прежнего формата; далее идут
«Статус прогона; Бизнес-результат; Assertions; Ожидалось; Получено; Источник evidence;
Причина отсутствия снимка; Источник ошибки; Сообщение прогона».
Assertions/expected/actual передаются JSON в корректно процитированных CSV-ячейках.
У отсутствующего сохранённого результата кейса статус отчёта `not_performed`,
сообщение «не выполнено: результата нет» — это не выдуманный passed/skipped.
Markdown экранирует HTML/ссылочную разметку из текста модели и 1С;
CSV нейтрализует начальные символы формул при открытии в электронной таблице.

Проверка подготовки C1: 16 DB-free unit-тестов отчётов; Chromium на реальных статических
файлах с ephemeral in-memory HTTP API проверил очередь, reload, отмену, terminal,
401/503, смену плана/страницы, новую запись rerun и безопасное отображение HTML.
Дополнительно на actual disposable app (PostgreSQL tmpfs, head 0010, проверенные
database/owner marker, три host uvicorn, только локальная GET-only OData fixture)
пройден путь project → ZIP import → audit → impact → local plan → approve metadata/manual
→ preflight 200 → enqueue 202 → polling → сохранённые typed assertions → MD/CSV download.
Проверка метаданных прошла, manual остался skipped; бизнес-успех не заявлялся.
На отдельном actual app также проверены reload/history и metadata failed (true → false).
Отдельный bounded continuation ниже проверил actual cancel/error/interrupted.
Реальная 1С/LLM, изменяющие действия и evidence веб-клиента не проверялись;
полная готовность C1 и выпуск D1/D2 этими результатами не подтверждаются.

Normal-flow evidence сохранён в `%TEMP%\testpilot-c1-own-9036cb9a61b8-55tik_wf`:
`probe.json`, `probe.md`, `probe.csv`, `probe.png`, `summary.json` и журналы сервисов.
Project `7681ec6f-12b0-4a80-9f33-80f9fdd0e75b`, suite
`693fa040-7f62-4ee8-a6a6-38186537002f`: completed, counts `1/0/0/1`,
metadata assertion boolean `true → true`, manual skipped, бизнес-успех не доказан.
В `summary.json` общий harness помечен failed из-за его собственного KeyError
при обращении к необязательному suite-полю `business_success` уже после сохранения
отчётов. Это не ошибка продукта и не доказательство отсутствующих edge-сценариев.
PostgreSQL и три процесса этого собственного nonce были очищены; proof-файлы сохранены.

После DIRECT Advisor SOURCE STABLE выполнен **один** bounded continuation:
`%TEMP%\testpilot-c1-missing-7419c40334fa-0myicsk_`, exit 0 за 345,89 с,
13 semantic checks true. Prerequisites создавались через actual API; normal UI flow
не повторялся. Новый собственный nonce `7419c40334fa877d`, PostgreSQL tmpfs/head 0010,
проверенные database/marker, три host uvicorn и localhost GET-only fixture.

- Cancel: UI queued → running → cancel_requested → cancelled; после текущего
  `$metadata` не отправлен следующий GET — всего один запрос. Reload сохранил статус.
- HTTP 500 от fixture: suite completed с counts `0/0/1/1`, кейс error и manual skipped;
  infrastructure error показана явно, бизнес-успех не заявлен.
- Остановлен только собственный intelligence во время delayed OData. UI показал 503
  и неизвестное состояние, не ложное завершение. После **реального** TTL300 intelligence
  перезапущен с той же PostgreSQL; reaper сохранил interrupted, повторного GET не было.
  Reload/history и MD/CSV по фактическим сохранённым кейсам проверены.
- В этом UI recovery нет предшествующего checkpoint. Отдельные PG/API-доказательства
  durable checkpoint не засчитываются как UI-проверка. JS errors: 0.

IDs и детали находятся в `summary.json`/`resources.json`; screenshots — `running.png`,
`cancelled.png`, `error.png`, `dependency-unavailable.png`, `interrupted.png`, `history.png`.
Результаты и MD/CSV сохранены для cancelled/error/interrupted. Собственный container
CREATE ID `43fa9e6e78d0` удалён после проверки ownership/marker, три процесса и fixture
остановлены, записанные порты закрыты, собственные stack temp files удалены;
`cleanup-proof.json` сохранён рядом с evidence. Чужие ресурсы не затрагивались.

Captured UI выявил два consumer-дефекта: stale payload.status=queued затирал
RunState.status=running после cancel, а cancelled-кейс показывался по-английски.
В `ai.js` исправлены приоритет RunState при merge и локализация terminal case status.
Отдельный DB-free browser smoke на actual static files подтвердил обе правки
(`ui-fix-local-proof.json`); повторный actual backend pass после них не выполнялся.


