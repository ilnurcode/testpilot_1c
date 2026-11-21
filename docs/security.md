# Безопасность: текущая постура

Документ фиксирует **как есть**. Ничего не настраивает и не выдаёт за готовое к внешнему хостингу.

## Текущая постура (факты)
- Compose публикует сервисы только на loopback: `127.0.0.1:8000` (web), `:8001` (impact), `:8002` (intelligence), `:5432` (PostgreSQL).
- TLS нигде не включён: uvicorn слушает HTTP без сертификатов, TLS-терминатора и редиректов в `compose.yaml`/`Dockerfile` нет. HTTPS используется только для исходящих запросов (LLM API); доверие к корпоративному CA задаётся `TP_1C_CA_FILE`.
- Аутентификация — HTTP Basic с тремя общими ролями (`analyst`, `expert`, `runner`); пароли из переменных окружения, локальные дефолты заданы в `compose.yaml`.
- Открыты без аутентификации: `/health`, `/static/*`, `GET /api/v1/audit/rules` (только перечень правил, без данных). `/docs`, `/redoc` и `/openapi.json` закрыты Basic Auth; `TP_OPENAPI_PUBLIC=1` открывает их — **только локально**. `/health` не отдаёт данные проектов: `status`, `executor_1c_env` (env-профиль), `projects_with_connection` (число) и `audit_max_issues` (предел детализации аудита, не секрет).
- Секреты сервисов живут в `.env` (в `.gitignore`) или переменных окружения контейнеров; portable key material подключения **не** хранится в `.env`, репозитории, Dockerfile или БД.
- Пароль подключения к 1С хранится только как encrypted envelope в `impact_projects.auth_secret`; legacy `auth_password` fail-closed и не используется. API отдаёт только `has_password`; credentials Codex — в отдельном named volume `codex_auth`.
- Пароли не пишутся в логи: логируется только warning сканера о нечитаемом файле; access-log uvicorn фиксирует метод, путь и статус, без заголовков и тел.
- URL-настройки (`TP_LLM_BASE_URL`, `TP_1C_ODATA_URL`, `TP_1C_WEB_URL`, `IMPACT_URL`, `INTELLIGENCE_URL`, `WEB_URL`, `DATABASE_URL`) проверяются на старте: нужна известная схема (http/https, для БД postgresql+psycopg/postgresql) и хост; опечатка вроде `htp://` или `localhost:8082` останавливает сервис, а не всплывает в рантайме.
- Числовые настройки окружения проверяются строго на старте (единый хелпер `testpilot/config.py::int_env`): `TP_AUDIT_MAX_ISSUES` — 100..20000, `TP_CODEX_TIMEOUT_SECONDS` — 1..900 (таймаут процессов Codex), `TP_1C_LOGIN_GRACE_SECONDS` — 2..60 (сколько ждать завершения обработки входа после единственной попытки, по умолчанию 30; состояние «в процессе» — disabled-поля/спиннер — считается ожиданием, а не ошибкой), `TP_1C_LOGIN_WAIT_SECONDS` — 5..120 (сколько ждать появления формы входа или рабочего стола после навигации, по умолчанию 25), `TP_1C_LOGIN_SUBMIT` — `click` или `enter` (способ отправки формы входа, по умолчанию `click`), `TP_1C_LOGIN_COOLDOWN_MINUTES` — 1..1440 и `TP_1C_LOGIN_MAX_FAILURES` — 1..10 (предохранитель от блокировки учётной записи: при N неудачных входах за M минут run-1c/rerun отвечает 429 до запуска браузера); неизвестное значение останавливает сервис с текстом «TP_1C_LOGIN_SUBMIT: ожидается click или enter»). Невалидное значение останавливает сервис с текстом «<VAR>=<значение>: нужно целое число от A до B», а не подменяется дефолтом.
- Объём хранилища от аудита ограничен `TP_AUDIT_MAX_ISSUES` (100..20000, по умолчанию 5000): в `code_audit.issues` и `impact_uploads.audit_result` сохраняются только первые N замечаний с фрагментами строк, поэтому скан файла в 1.6 МБ (200 000 замечаний) не раздувает БД и ответы. Полные счётчики `total_issues`/`issues_by_severity` при этом не теряются; невалидное значение останавливает запуск сервиса.

### Коды без учётных данных (живая проверка)
| Маршрут | Без креды |
|---|---|
| `GET /health` | 200 |
| `GET /` | 401 |
| `GET /static/audit.js` | 200 |
| `GET /docs`, `GET /redoc`, `GET /openapi.json` | 401 (200 только с `TP_OPENAPI_PUBLIC=1`) |
| `GET /api/whoami` | 401 |
| `GET /api/v1/audit/rules` | 200 |
| `GET /api/v1/audit/scans?limit=1` | 401 |
| `GET /api/v1/audit/scans/{id}/report` | 401 |
| `GET /api/v1/audit/uploads/{id}/report` | 401 |
| `POST /api/v1/audit/scan` | 401 |
| `GET /api/v1/projects/{id}` (8001) | 401 |
| `GET /api/v1/ai/suites/{id}`, `…/report`, `/ai/evidence/{id}` (8002) | 401 |
| `GET /gateway/impact/api/v1/versions` | 401 |
| `DELETE /api/v1/projects/{id}?confirm={id}` (8001) | 401 |

## Имена переменных с дефолтами (значения не публикуются)

`POSTGRES_PASSWORD`, `TP_ANALYST_PASSWORD`, `TP_EXPERT_PASSWORD`, `TP_RUNNER_PASSWORD` — непустые локальные дефолты в `compose.yaml`. Пустые по умолчанию: `TP_LLM_API_KEY`, `TP_1C_PASSWORD`, `TP_1C_AUTHORIZATION`. В `.env.example` `DATABASE_URL` содержит `CHANGE_ME`.

## Backup/restore (A2)

- Backup адресует только источник с positive identity: имя БД + `COMMENT ON DATABASE` marker, равный ожидаемому instance token. Несовпадение — отказ до дампа.
- Дамп и metadata читаются с одного `pg_export_snapshot`, удерживаемого до завершения pg_dump и подсчёта counts/payload/evidence; рабочая БД не останавливается.
- Дамп пишется потоком во временную директорию, затем dump.sql + manifest.json (format, size, sha256, database, marker, alembic revision, counts, md5 payload projects/suites, sha256 каждого evidence) публикуются одной директорией атомарным rename. Схема манифеста проверяется целиком до записи: отсутствие payload/evidence, неверные типы/format отклоняются. Неуспешный/частичный backup не оставляет валидной конечной директории.
- Restore принимает только backup-директорию с manifest, проверяет size/sha256 дампа, identity target и пустой public, выполняет dump и проверки counts/payload/evidence/alembic в одной psql-транзакции (`--single-transaction` + `ON_ERROR_STOP`) до COMMIT; при любом расхождении транзакция откатывает и dump, и проверки, target остаётся пустым, marker сохраняется. Непустой target не перезаписывается.
- Ручной backup рабочего сервиса — отдельное действие владельца: `TP_BACKUP_MANUAL=1` и явный `TP_BACKUP_CONFIRM_PROJECT`, только read-only snapshot; без подтверждения backup отказывает. В рамках A2 реальный рабочий backup не запускался.
- Дамп содержит только зашифрованные колонки `auth_secret`/`auth_secret_scheme` и не содержит portable key. Открытый текст возможен лишь в legacy-колонке `auth_password` у строк, созданных до A4 и ещё не прошедших явную миграцию; в проверенном A4-сценарии она `NULL`. Backup-директорию всё равно хранить как секрет и не публиковать.

## Что обязательно изменить при выносе из localhost

1. Заменить все дефолтные пароли (`POSTGRES_PASSWORD`, `TP_ANALYST_PASSWORD`, `TP_EXPERT_PASSWORD`, `TP_RUNNER_PASSWORD`) и ключ `TP_LLM_API_KEY`; ротировать `.env` и пароль БД.
2. Поставить HTTPS-терминатор перед web/API, не публиковать `8001`/`8002` напрямую, включить HSTS и редирект с HTTP.
3. Убрать публикацию `5432` на хост, оставить БД во внутренней сети Compose.
4. Не включать `TP_OPENAPI_PUBLIC` на внешнем хосте: `/docs`, `/redoc` и `/openapi.json` раскрывают полную схему API и по умолчанию требуют Basic Auth именно для этого.
5. Ротировать пароли подключения 1С после любого дампа БД, бэкапа `pgdata` или доступа к хосту: строки, созданные до A4, лежат в `impact_projects.auth_password` открытым текстом до явной миграции.
6. Ввести персональные учётные записи и разделение доступа: сейчас любая роль читает все проекты, отчёты о прогонах и evidence (скриншоты).
7. Защитить доступ к named volume `codex_auth` (вход ChatGPT общий для стенда) и предусмотреть secret-store для пароля 1С.

## Что уже сделано

- Basic Auth на изменяющих и читающих маршрутах; открыты только `/health`, статика и список правил. Схема API и UI документации закрыты по умолчанию.
- Удаление проекта требует роль `expert` и `confirm=<project_id>` (иначе 409) и выполняется одной транзакцией.
- `open_url` в сценариях ограничен origin веб-клиента проекта; внешние адреса отклоняются.
- Полный исходник вставленного кода не хранится: только отпечаток входа, сводка и фрагменты строк ≤140 символов; SEC013 маскирует значение секрета (`"***"`).
- Прогон на базе 1С требует явного `allow_mutations` для изменяющих шагов, а `strict_read_only` разрешает только проверки без кликов.
- Передача контекста внешнему провайдеру требует явного `consent`; API не возвращает пароли (`has_password`), `.env` исключён из Git. `PUT /api/v1/ai/settings/llm` по умолчанию меняет настройки только в памяти: запись в файл окружения — явный opt-in (`TP_ALLOW_ENV_WRITE=1` + `persist=true`), потому что скрытая запись секрета на диск из UI была опасным дефолтом.
- Для небезопасных методов включена проверка `Origin` (same-origin middleware).

## Чего нет
- Нет TLS и нет защиты от перехвата трафика внутри сети; схема API закрыта только общим паролем — с любой ролью `/openapi.json` отдаётся целиком.
- Нет rate-limit и защиты от подбора пароля; неудачные попытки входа не логируются, аудит фиксирует роль, а не сотрудника.
- Нет ролевого разделения на чтение (analyst видит всё, что видит expert, кроме изменяющих операций).
- Нет CSRF-токенов: защита только проверкой `Origin`; встраивание в сторонний iframe не блокируется.


## Секреты подключения и границы сети (A4)

- Новый пароль подключения не сохраняется открытым текстом. Пока migration `0010` не применена, `PUT /api/v1/projects/{id}/connection` с паролем отвечает 503 и ничего не пишет; URL без пароля сохраняются как раньше.
- На Windows default-протектор — DPAPI пользователя (`dpapi-user-v1:<base64>`); старые DPAPI envelopes остаются читаемыми. Перенос на другой Windows profile даёт явный отказ и требует повторного ввода пароля владельцем.
- Portable-протектор — `portable-aesgcm-v1:<base64url>` с AES-256-GCM, случайным nonce и AAD, привязанным к project ID. Он доступен только при явном `TP_SECRET_PROTECTOR=portable` либо на non-Windows в `auto`, если валиден `TP_PORTABLE_SECRET_KEY_FILE`.
- Key file содержит ровно один externally provisioned 32-byte key в URL-safe base64, должен находиться вне checkout, быть regular file и на POSIX иметь owner-only permissions. Он не генерируется при старте; создать его можно только явно: `python scripts/secrets_key.py --create-portable-key --key-file <absolute external path> --confirmed`. Потерянный/неверный/небезопасный key file даёт fail-closed отказ без plaintext, пути или key material.
- Проверка и explicit legacy migration: `python scripts/secrets_key.py --status|--verify|--migrate-legacy --confirmed`; сначала шифрование и проверенный roundtrip, только затем `auth_password = NULL`. DPAPI→portable rekey/migration автоматически не выполняется.
- Идентичность цели для consent/approval: `connection_public_identity()` возвращает
  `{odata_url, web_url, auth_username, has_password, connection_revision}`, а
  `target_identity()` — канонический checksum публикации, нормализованного principal и версии
  соединения. Секрет и его хеш не участвуют; `project_id` сам по себе идентичностью не является.
- Политика сетевых целей (`testpilot/target_policy.py`): `TP_TARGET_ALLOWLIST` (host[:port]) —
  единственный способ разрешить внешний хост, `TP_ALLOW_LOCAL_TARGETS=1` — loopback-стенд.
  Отказ происходит **до отправки**: не http(s), userinfo в URL, хост вне allowlist. Редирект
  не преследуется; cross-origin переход считается новой целью и отклоняется, credentials на
  другой origin не пересылаются. URL и заголовки в сообщениях редактируются (`userinfo`, query).
- API проектов не содержит пароля и password-hash; `has_password` считается из зашифрованной
  или legacy-записи одним запросом на страницу (без N+1). Терминальные статусы прогона теперь
  включают `cancelled` и `interrupted`, поэтому последний прогон не скрывает отмену/сбой за
  более старым `completed`.

### Portable key: владелец, хранение и перенос

- Владелец. Key file создаёт только владелец стенда явной командой
  `python scripts/secrets_key.py --create-portable-key --key-file <absolute external path> --confirmed`.
  Файл создаётся с режимом `0600` и принадлежит тому UID, под которым работает приложение
  (в контейнере это root, если образ не сбрасывает пользователя). Чтение требует regular file,
  `st_uid == euid` и отсутствия group/other-битов; symlink, относительный путь, путь внутри
  checkout и файл больше 128 байт отклоняются. Поэтому на Windows bind-mount Docker Desktop
  с правами `0777` portable-режим закономерно отказывает: нужен POSIX-том/WSL или DPAPI-профиль.
- Где хранить. Отдельно от БД и от поставки: менеджер секретов или защищённый носитель
  владельца. Key file не входит в git, в образ, в SQL-дамп, в backup-директорию и в
  staging-пакет; в дампе и пакете остаются только ciphertext-колонки и имена переменных.
  Ключ не публикуется и не пересылается в чате/тикете.
- Как переносить. Backup/restore переносит только БД: `dump.sql` + `manifest.json`. Ключ
  переносится отдельным каналом (`cp`/`scp`/secret manager) в файл по пути
  `TP_PORTABLE_SECRET_KEY_FILE`. Порядок восстановления: поднять пустую БД с тем же
  `COMMENT ON DATABASE` marker → `scripts/restore.sh|ps1 --dump <dir> --yes` → положить ключ на
  место. Ключ читается на каждый вызов, поэтому перезапуск приложения не требуется.
- Без ключа. Чтение секрета и запись нового пароля дают `503` с текстом
  `Portable key material недоступен.`; `scripts/secrets_key.py --status` и `--verify`
  завершаются кодом 3 и печатают ту же причину в `BLOCKED`. Сохранённый ciphertext не меняется.
- С неверным ключом. AES-GCM даёт `InvalidTag` → `503`
  `Portable secret не расшифровывается с текущим key material.`, `--verify` завершается кодом 2
  с `FAIL <project id>`; строка в БД остаётся байт-в-байт прежней.
- Перезапись без подтверждения. Если `auth_secret` уже есть и не расшифровывается текущим
  протектором (чужой ключ, несовместимая схема), `PUT .../connection` с новым паролем отвечает
  **409** с текстом, требующим проверенного backup и `rekey_confirm=true`. Транзакция
  откатывается целиком: прежний `auth_secret` остаётся байт-в-байт, меняются ноль строк.
  Это защищает от молчаливой потери единственной копии пароля.
- Осознанный rekey. После проверенного backup — тот же `PUT` с явным флагом:
  `{"odata_url": ..., "auth_username": ..., "auth_password": "<новый>", "rekey_confirm": true}`.
  Секрет шифруется текущим ключом, `auth_secret_scheme`/`auth_secret_updated_at` обновляются,
  в аудите `update_connection` фиксируется `rekey_confirmed: true`, прежний шифротекст не
  сохраняется. Без подтверждения данные не меняются.
- Отсутствующий ключ. Если key file недоступен, протектор не считается доступным, поэтому
  guard не подменяет `503` на `409`: запись (в том числе с `rekey_confirm=true`) отклоняется
  `503`, ciphertext не меняется.
- Старые записи. Restore не переписывает секреты: DPAPI-envelope остаётся DPAPI-envelope,
  portable — portable. Автоматического DPAPI→portable rekey нет: запись DPAPI читается только
  на Windows под тем же профилем, иначе отказ без подстановки текста. Переход на portable —
  повторный ввод пароля владельцем; legacy-строки — `--migrate-legacy --confirmed` после
  проверенного backup.
- Проверено 2026-11-20 в одноразовых Linux-контейнерах (POSIX-том, не bind-mount): ключ создан
  штатной командой с правами `root:root 0600`; backup 37 069 байт без key material и без
  открытого пароля; restore в чистую БД с отдельно перенесённым ключом дал совпадение
  расшифрованного пароля; без ключа и с чужим ключом — `503`, ciphertext не изменился
  (sha256 до/после равны); staging-пакет (102 файла) ключа и пароля не содержит. Нативный
  Windows-хост в portable-режиме этой проверкой не покрыт.
