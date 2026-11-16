# TestPilot 1C

Рабочий учебный MVP: сравнение двух нормализованных конфигураций, объяснимое влияние по графу, HTTP-передача зоны, приоритизация и оптимизация регрессионного набора с сохранением в PostgreSQL. Два самостоятельных FastAPI API и русскоязычный web-интерфейс. Подключение к 1С настраивается в проекте; проверенная приёмка DemoTrd приведена ниже. Данные демонстрации явно помечены synthetic.

## Самый простой запуск на Windows

Подробные инструкции: [PDF](docs/guides/TestPilot_1C_Windows_Guide.pdf) и [Word](docs/guides/TestPilot_1C_Windows_Guide.docx).

1. Установите и откройте [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/), Linux containers.
2. Скачайте [ZIP проекта](https://github.com/ilnurcode/testpilot_1c/archive/refs/heads/main.zip) и распакуйте папку.
3. Дважды щёлкните `Run-TestPilot.bat`. Он соберёт и запустит весь стек, дождётся готовности и откроет браузер. Первый запуск скачает образы, Python-пакеты, Chromium и фиксированную версию Codex CLI и может занять несколько минут.
4. Вход: `expert` / `TestPilotLocal123!` (если нет пользовательского файла .env).
5. Для штатной остановки дважды щёлкните `Stop-TestPilot.bat`. PostgreSQL и файлы проекта сохраняются.

Нужен только Docker Desktop, не нужны локальные Python/Git/Ubuntu/команды. Подробности и устранение ошибок — [руководство для Windows](docs/windows.md). Продвинутый вариант через локальный Python/PowerShell — [windows-powershell.md](docs/windows-powershell.md).

## Презентационный режим

Проверенный запуск текущего этапа:

```bash
cd /workspace/testpilot_1c
./scripts/start-presentation.sh
```

В интерфейсе: **Обзор → Открыть демонстрацию → Подготовить проверки → Сформировать сценарии → подтвердить → Запустить на учебном стенде**. Выполняются реальные HTTP- и Playwright-проверки отдельного synthetic-приложения, сохраняются результаты и скриншоты. Внешняя нейросеть подключается через Codex CLI с входом ChatGPT без API-ключа либо независимо через серверный TP_LLM_API_KEY. Без подключения доступен явно маркированный учебный генератор. Реальная 1С не имитируется.

[Пошаговый сценарий показа, настройка API и границы](docs/presentation.md).

## Проекты и сохранённые загрузки

Откройте **«Проекты и файлы»**, создайте проект и загрузите разработку. Интерфейс сохраняет исходный файл, показывает историю версий, распознаёт дубликаты и предлагает предыдущую загрузку того же компонента. Можно выбрать другую базу или полный анализ. Файлы и отчёты остаются в PostgreSQL после обновления страницы и перезапуска приложений.

CFE/CF/EPF/ERF/DT и ZIP принимаются на хранение до 64 МиБ. Анализ доступен для нормализованного JSON до 8 МиБ или ZIP с `configuration.json` в корне. Если в бинарном контейнере 1С обнаружены модули BSL, версия получает статус «Готов к анализу». Если модулей BSL нет, загрузка получает статус `metadata_only`, а аудит возвращает `no_bsl_modules` и `passed: false`; метаданные контейнера не выдаются за разобранные объекты и связи. Установка и запуск в 1С не выполняются.

Подробности, ограничения и API: [библиотека файлов](docs/uploads.md). Compose-сервис migrate применяет актуальные Alembic-ревизии. Не удаляйте PostgreSQL volume.

## Демонстрационный аудит

Синтетическая выгрузка исходников: ZIP `samples/demo_vulnerable_extension.zip` и тот же код распакованным каталогом `samples/demo_vulnerable_extension/`. Сценарий показа: загрузить ZIP в проекте, запустить аудит, затем «Рассчитать область влияния».

Фактический результат на текущих исходниках — **11 замечаний в 8 проверенных файлах**: `SEC001` (CRITICAL), `SEC005`, `v8std:privileged-mode`, `v8std:query-in-loop`, `v8std:server-in-client-loop`, `v8std:virtual-tables-params`, `SEC011` (HIGH), `v8std:unconditional-server-context` (MEDIUM), `v8std:naming`, `v8std:empty-exception-handler`, `STD001` (LOW). Это 1 CRITICAL, 6 HIGH, 1 MEDIUM и 3 LOW.

`SEC002` и `v8std:managed-lock-in-posting` на этих исходниках не срабатывают: `SEC002` проверяет однострочную конкатенацию `Запрос.Текст` с `+`, а `SEC011` — только многострочную, поэтому на этом присваивании нет дублирующего срабатывания. Учебная процедура проведения называется `ПримерОбработкиПроведения`. Комментарии-маркеры в коде описывают замысел примера, а не результат аудита. Полный список с файлами и строками — в [README выгрузки](samples/demo_vulnerable_extension/README.md).

Аудит ZIP проверяет `.bsl`/`.os`/`.xml` внутри архива. Если загружен бинарный контейнер и модулей BSL в нём не найдено, ответ содержит `status: "no_bsl_modules"`, `passed: false` и объяснение: проверять было нечего, дефектов не «ноль».

Вставленный код проверяется через `POST /api/v1/audit/scan` (Basic Auth: запись в журнал). Полный исходник не сохраняется; сохраняются отпечаток проверенного входа, сводка и фрагменты найденных строк — до 140 символов. Журнал: `GET /api/v1/audit/scans`, отчёт: `GET /api/v1/audit/scans/{id}/report?format=md|csv`. Подробности и коды ошибок — [docs/api.md](docs/api.md).

## Приёмка на демо-базе (DemoTrd)

Запуск стека: `docker compose up -d --build --wait`; UI: `http://127.0.0.1:8000`. Публикация 1С — `http://localhost:8088/DemoTrd`; Apache работает вне Compose.

В настройках подключения проекта задайте:

- `web_url`: `http://localhost:8088/DemoTrd`
- `odata_url`: `http://localhost:8088/DemoTrd/odata/standard.odata`
- `auth_username`: `Администратор`

Пароль в документацию не включайте.

Для зелёного сценария выберите provider `llm`, подтвердите согласие на передачу контекста, одобрите только неразрушающий Playwright-сценарий навигации и вызовите `POST /suites/{sid}/run-1c`. Проверенный сценарий «Продажи» завершился с `passed=1`; PNG сохраняется как evidence.

`assert_section` сначала проверяет класс `select` выбранного пункта меню; если активного маркера нет, подтверждает только новый текст раздела, появившийся в рабочей области `#funcPanel`.

Ограничения DemoTrd: `$metadata` содержит 0 EntitySet, поэтому отсутствующие OData-сущности возвращают честный HTTP 404. Бинарный `.cfe` без BSL получает `metadata_only`, а аудит — `no_bsl_modules` с `passed=false`. В контейнере адреса `localhost` и `127.0.0.1` автоматически маршрутизируются как `host.docker.internal`.

Пул тестов: `docker compose exec -T intelligence python -m pytest tests/ -q` — 180 passed, 1 skipped.

## Состав

- `testpilot/impact` — импорт, версии, сравнение, NetworkX-граф, пути влияния, отчёт и доставка.
- `testpilot/intelligence` — каталог/покрытие, история/дефекты, скоринг, оптимизация, ML и будущий runner-контракт.
- `testpilot/web` — девять рабочих разделов; данные только из публичных API.
- `contracts` — JSON Schema 1.0 и синтетические примеры; общие Pydantic-модели в `testpilot/contracts.py`.
- `migrations` — 0001: impact и аудит; 0002: test intelligence; 0003: проекты и постоянная библиотека файлов; 0004: планы проверок, скриншоты и учебный стенд.
- `tests`, `scripts`, `docs` — проверки, демо, замеры и описание.

В Docker-варианте достаточно Docker Desktop с Compose v2; PostgreSQL 17 и Chromium ставятся внутри контейнеров. Для запуска Python-приложений непосредственно на хосте нужен Python 3.12+.

## Ручной запуск Docker Compose

Из папки проекта команда вручную, эквивалентная двойному щелчку по Windows-лаунчеру:

```bash
docker compose up --build --detach --wait --wait-timeout 900 impact intelligence web
```

Откройте `http://127.0.0.1:8000`. Без собственного `.env` локальная учётная запись — `expert` / `TestPilotLocal123!`. Эти настройки подходят для локальной демонстрации и привязаны к loopback; не публикуйте порт наружу. Собственный `.env` может переопределить локальные пароли и API-ключ. PostgreSQL volume сохраняется после `docker compose down`; не удаляйте его опцией `-v`.

OpenAPI каждого API: `/openapi.json`, интерактивная документация: `/docs` на портах 8001 и 8002. Web — порт 8000. Health: `/health` каждого процесса, проверяет PostgreSQL и наличие миграций. При запуске вне localhost нужен HTTPS reverse proxy.

## Codex CLI: вход через ChatGPT

Это отдельный источник генерации наряду с API-провайдером и учебными правилами. API-ключ не нужен: официальный CLI использует доступ Codex вашей учётной записи ChatGPT. Доступность моделей, квоты и ограничения зависят от текущего плана, workspace и политик организации; подписка не означает безлимитную генерацию и не заменяет оплату независимого API. При исчерпании лимита, отсутствии входа или ошибке CLI TestPilot возвращает ошибку, а не переключается на другой источник.

### Docker: вход из интерфейса

1. Запустите обычный Compose-стек. Образ устанавливает **`@openai/codex@0.167.2`** через npm в стадии Node 22 Debian и переносит Node и пакет вместе с native optional dependency для целевой платформы (`linux/amd64` или `linux/arm64`). Загрузки `latest` и установки CLI на хост для Docker не требуется. Offline-образ для разработки CLI не содержит.
2. Войдите в TestPilot как **expert**, откройте **«Нейросеть и проверки»** и запустите вход ChatGPT в блоке Codex.
3. Разрешите **device code login** в настройках безопасности ChatGPT; для workspace разрешение может выдавать администратор. Откройте официальный адрес, показанный UI, и введите там одноразовый код. Вход завершает пользователь в браузере, TestPilot не запрашивает пароль ChatGPT.
4. Дождитесь статуса подключённого Codex, выберите его как источник генерации, явно подтвердите отправку контекста и сформируйте сценарии. Полученные сценарии — черновик: перед исполнением по-прежнему требуется экспертное подтверждение.

Вход ChatGPT **общий для локального стенда**, а не персональный для каждой роли TestPilot. Доступ к стенду даёт возможность использовать подключённый аккаунт и его квоты; не публикуйте сервис в интернет. В UI выход Codex доступен эксперту: он отменяет незавершённый вход и удаляет сохранённые credentials. Это не выход из ChatGPT в браузере и не удаление проектов.

Только `intelligence` получает `CODEX_HOME=/var/lib/testpilot/codex` и named volume `codex_auth` (при стандартном имени Compose — `testpilot_codex_auth`). В этом каталоге CLI хранит файл `auth.json` с токенами: защищайте volume как пароль, не публикуйте его, не копируйте credentials с хоста и не монтируйте `~/.codex`. `Stop-TestPilot.bat`, `docker compose stop`, `restart` и `down` **без `-v`** сохраняют вход и volume. Для отключения аккаунта сначала используйте выход Codex в UI; удалять volume для выхода не нужно. `down -v` удаляет также PostgreSQL и данные проектов — не используйте его для выхода.

Настройки `intelligence` в `.env`: `TP_CODEX_BIN=codex`, `TP_CODEX_MODEL=` (пустое значение — модель по умолчанию учётной записи), `TP_CODEX_TIMEOUT_SECONDS=180`. После изменения пересоздайте сервис обычной командой `docker compose up -d intelligence`; существующий auth volume останется. Не задавайте для этого источника `OPENAI_API_KEY` или `CODEX_API_KEY`.

### Запуск Python-приложений на хосте

CLI и вход устанавливаются отдельно от Python-зависимостей. Например, в PowerShell установите Node.js 22 и выполните:

```powershell
npm install --global @openai/codex@0.167.2
$env:CODEX_HOME = Join-Path $env:LOCALAPPDATA "TestPilot\codex"
codex -c forced_login_method=chatgpt -c cli_auth_credentials_store=file login
```

Для Linux задайте отдельный каталог командой `export CODEX_HOME="$HOME/.local/share/testpilot/codex"`, затем используйте те же команды `npm install` и `codex ... login`. Завершите браузерный вход именно **ChatGPT**, не API key. При отсутствии локального callback используйте вход из UI через device code с разрешением в ChatGPT Security. CLI-вход и процесс `intelligence` должны работать от одного пользователя с **одинаковым абсолютным `CODEX_HOME`**; запускайте host-сервисы из той же оболочки либо передайте эту переменную их окружению. Используйте отдельный каталог TestPilot, не меняя существующие настройки OMP/host Codex. Для ручного выхода из этого каталога: `codex -c cli_auth_credentials_store=file logout`; также доступен выход из UI.

### Передаваемый контекст и безопасность

Согласие в UI требуется для каждого запроса к Codex, как и для внешнего API. В OpenAI передаётся ограниченный контекст изменений, графа, каталога и разрешённых фрагментов модели (не более 100 КБ), а не только текст запроса; не подтверждайте отправку конфиденциальных данных без разрешения. ChatGPT sign-in не делает обработку локальной. Учётные данные CLI не возвращаются API и не включаются в контекст.

Генерация идёт через stdin во временном каталоге без исходников и секретов, с read-only sandbox, отключёнными shell/web search и без пользовательских конфигураций, правил, MCP, hooks и skills. CLI не получает полномочий исполнять сгенерированный код; ответ проходит схему сценария и существующий процесс экспертного подтверждения. Статус подключения означает только доступность CLI и вход, а не подтверждённый успешный вызов модели.

API: `GET /api/v1/ai/codex/status`, `POST /api/v1/ai/codex/login`, `POST /api/v1/ai/codex/logout`; операции входа/выхода требуют expert. Общий `GET /api/v1/ai/status` включает статус Codex. В генерации используется `provider: "codex"` с явным согласием.

Официальные сведения: [аутентификация и device flow](https://learn.chatgpt.com/docs/auth.md), [неинтерактивный режим](https://learn.chatgpt.com/docs/non-interactive-mode.md), [доступ и лимиты](https://learn.chatgpt.com/docs/pricing).


## Разработка в облачной Linux-среде

В этой изолированной среде Docker build не может разрешить внешний DNS. Поэтому здесь использовался проверенный вариант: процессы Python на хосте и PostgreSQL в Docker. Он предназначен для разработки; его образ без Chromium нельзя использовать как проверку полного браузерного Docker-исполнителя.

```bash
cd /workspace/testpilot_1c
export UV_CACHE_DIR=/tmp/testpilot-uv
export UV_TOOL_DIR=/tmp/testpilot-tools
export DOCKER_CONFIG=/tmp/testpilot-docker
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python scripts/local_env.py
mkdir -p .wheelhouse
uvx --from pip pip download --dest .wheelhouse -r requirements.lock 'setuptools>=75' wheel
docker compose -f compose.yaml -f compose.offline.yaml up -d --build --wait
set -a
. ./.env
set +a
.venv/bin/python scripts/smoke.py
```

`.wheelhouse` и `.env` исключены из Git. Этот offline-вариант переносит Python-приложения в контейнеры, но не включает проверенный Chromium и не является целевым способом презентационного запуска. Обычный Windows-лаунчер собирает полный образ с Chromium; в изолированной облачной среде DNS пакетов из Docker build закрыт, поэтому эта сборка здесь не прошла.

## Полная демонстрация

В интерфейсе под ролью expert: **Обзор → Учебная демонстрация полного сценария → Выполнить учебный сценарий**. Последовательность выполняет реальные HTTP-запросы:

1. Импорт двух версий `demo-sales` и 16 сценариев, экспертной учебной карты покрытия и 12 синтетических прогонов.
2. Сравнение, анализ прямого/косвенного влияния, сохранение объясняющих путей.
3. Доставка пакета Impact → Intelligence через HTTP.
4. Расчёт плана: бюджет 330 секунд, порог 90% доступных подтверждённых объектов.

CLI при запущенных сервисах:

```bash
set -a
. ./.env
set +a
.venv/bin/python -m testpilot.demo
# Или health + тот же сценарий с проверкой результата:
.venv/bin/python scripts/smoke.py
```

Демо не загружается автоматически при старте. Команда работает только с `demo-sales` и ID `demo-*`; повторные идентичные импорты не дублируют версии/историю, новый расчёт создаёт новый план. Повторная загрузка не перезаписывает экспертные решения по каталогу и покрытиям. Если вы изменили демо-каталог, ожидаемые assert демо могут перестать выполняться — это проверка изменившихся данных, а не повод стирать их.

После загрузки можно пройти разделы **Сравнение → Граф и влияние → Каталог и покрытия → История и дефекты → Расчёт тест-плана → Отчёты**. Граф поддерживает фильтры, масштаб, перемещение и просмотр путей по клику на узел. Сложные карточки тестов и пакеты истории редактируются как JSON с серверной валидацией. Эксперт может менять состав плана; нарушение mandatory/бюджета/покрытия требует явного разрешения с причиной. Подтверждённый план не меняется. Экспорт задания будущему runner ничего не запускает и не создаёт `passed`.

## Разработка без сборки приложений в Docker

Остановите контейнеры приложений, оставив БД, чтобы не занять те же порты:

```bash
docker compose stop web impact intelligence
docker compose up -d db
set -a
. ./.env
set +a
.venv/bin/alembic upgrade head
./scripts/start-local.sh
.venv/bin/python scripts/smoke.py
```

`start-local.sh` запускает три uvicorn на loopback, хранит PID/логи в `.runtime` и проверяет health; повтор не создаёт ещё один процесс с живым PID. Останавливайте только запущенные вами PID. Сервисы берут адреса и пароли из env. `DATABASE_URL` на хосте указывает на 127.0.0.1; внутри Compose он переопределяется адресом `db`. Адрес Intelligence для доставки задаётся серверной переменной, не произвольным URL пользователя.

## Проверки

При доступной PostgreSQL и загруженном `.env`:

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/benchmark.py
.venv/bin/alembic check
```

API-тесты создают отдельную случайную схему `tp_test_*` и удаляют только её. Используйте роль PostgreSQL с правом CREATE SCHEMA. Тесты не удаляют пользовательские таблицы и не используют живую 1С. При отсутствии DATABASE_URL проверки БД завершаются ошибкой, а не скрытым пропуском.

Дополнительно в контейнере (сервисная DATABASE_URL уже настроена):

```bash
docker compose exec -T intelligence pytest -q
docker compose exec -T impact python scripts/benchmark.py
```

Браузерный smoke, если установлен Chromium:

```bash
uv pip install --python .venv/bin/python playwright
TP_CHROMIUM_PATH=/usr/bin/chromium .venv/bin/python scripts/browser-smoke.py
```

Playwright и Chromium входят в полный Docker-образ; на хосте Linux браузерный smoke требует установки Playwright и Chromium. Скрипт действительно выполняет демо, сравнение, граф, ML, план и экспертное подтверждение на учебных данных. Скриншот сохраняется в `.runtime/web-plan.png`.

## Сохранность и эксплуатация

`docker compose restart` сохраняет PostgreSQL volume, результаты и отдельный Codex auth volume. Для остановки без удаления данных — `docker compose down` (без `-v`); вход ChatGPT при этом сохраняется. Для отключения аккаунта используйте выход Codex в UI. Не удаляйте volume для «исправления» конфликтов импорта. Другой пакет с тем же номером версии получает 409; используйте новый номер версии. История и отчёты immutable; тесты удаляются логическим архивированием.

Параметры: `DATABASE_URL`, `TP_ANALYST_PASSWORD`, `TP_EXPERT_PASSWORD`, `TP_RUNNER_PASSWORD`, `IMPACT_URL`, `INTELLIGENCE_URL`; `WEB_URL` — только для smoke. Секреты не встроены в исходники, Dockerfile и инструкции. Не включайте `.env` в Git или Docker build context.

Подробнее: [архитектура](docs/architecture.md), [алгоритмы](docs/algorithms.md), [API/контракты](docs/api.md), [соответствие учебным материалам](docs/requirements-map.md), [будущая 1С](docs/1c-integration.md), [ограничения](docs/limitations.md), [фактические проверки](docs/validation.md).

## Первый шаг подключения реальной 1С

Целевой workflow с расширениями, OData и Playwright описан в [connected-workflow.md](docs/connected-workflow.md). Для подключённой базы доступен запуск через `POST /suites/{sid}/run-1c`; проверенная приёмка DemoTrd приведена выше. Другие публикации и конфигурации следует проверять отдельно.

Добавлена read-only диагностика публикации:

```bash
# Задайте TP_1C_ODATA_URL и при необходимости TP_1C_WEB_URL в защищённой среде.
# Схемы переменных аутентификации — в .env.example. Значения в чат не передавайте.
.venv/bin/python -m testpilot.integration.probe
```

Команда выполняет GET `$metadata` и GET страницы веб-клиента, выводит опубликованные EntitySet, различает 401/403/сетевую ошибку/неправильный XML. Не изменяет данные, не следует redirect с паролем и не выдаёт страницу входа за успешную аутентификацию. Проверка веб-страницы выполняется без переноса OData-пароля. Доступность `$metadata` не подтверждает права на данные, версию BSL/расширений или работу Playwright-тестов. В проверенной DemoTrd-публикации GET `$metadata` вернул HTTP 200 и 3322 байта, но EntitySet отсутствуют.


Проверка новой библиотеки в браузере (Playwright и Chromium):

```bash
.venv/bin/python scripts/projects-browser-smoke.py
```

Она создаёт отдельный явно синтетический проект и проверяет загрузки, полный/дифференциальный анализ, скачивание исходных байтов, повтор загрузки, обновление страницы, поиск и мобильную ширину. Локальные скриншоты сохраняются в `.runtime/projects-desktop.png` и `.runtime/projects-mobile.png`.
