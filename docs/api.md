# API и контракты 1.0

Все бизнес-запросы требуют Basic Auth; `/health`, `/docs`, `/openapi.json` доступны без аутентификации. Ошибки: `{"error":{"code":422,"message":"…","fields":[…]}}`; поля подробностей могут быть списком в `message`. Коды 401/403 — доступ, 404 — отсутствующая запись, 409 — конфликт, 413 — размер, 422 — схема, 502/503 — доставка. Успешные операции JSON обычно возвращают 200.

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

## Checksum и обмен

`ImpactPackage` содержит schema_version, package_id, configuration_id, base_version, target_version, analysis_id, created_at, synthetic, changed_objects, affected_objects, dependency_paths, warnings, checksum. JSON Schema в `contracts`, строгие проверки ссылок и checksum в Pydantic.

Контрольная сумма — SHA-256 UTF-8 JSON после валидации модели, без поля checksum; `ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')`, `allow_nan=False`. Даты сериализуются Pydantic ISO 8601 (UTC — Z). Списки экспортного пакета уже детерминированно упорядочены алгоритмом, потребитель их не переставляет. Эталонная реализация — `testpilot.contracts.checksum`. Это контроль целостности, не цифровая подпись.

Повтор одного package_id с тем же checksum не дублируется. Другой checksum того же ID — 409. Сохранённый анализ доступен при сетевой ошибке отправки; повторяется только `/deliver`.

`contracts/execution-job-1.0.schema.json` и `execution-report-1.0.schema.json` описывают будущий runner. Артефакты — только строки HTTPS, сервис их не скачивает. Не передавайте секреты внутри executor_parameters: используйте отдельный защищённый канал будущего исполнителя.


## Проекты и файлы

Добавлены API библиотеки проектов и оригинальных загрузок. Полное описание параметров, режимов и ограничений — [uploads.md](uploads.md). Тело upload передаётся исходными байтами, а не multipart. Обычные JSON API сохраняют лимит 8 МиБ; файловая загрузка ограничена 64 МиБ.
