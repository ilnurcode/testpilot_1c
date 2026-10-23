# TestPilot 1C

Рабочий учебный MVP: сравнение двух нормализованных конфигураций, объяснимое влияние по графу, HTTP-передача зоны, приоритизация и оптимизация регрессионного набора с сохранением в PostgreSQL. Два самостоятельных FastAPI API и русскоязычный web-интерфейс. **Исполнитель 1С не подключён.** Данные демонстрации явно помечены synthetic.

## Самый простой запуск на Windows

Подробные инструкции: [PDF](docs/guides/TestPilot_1C_Windows_Guide.pdf) и [Word](docs/guides/TestPilot_1C_Windows_Guide.docx).

1. Установите и откройте [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/), Linux containers.
2. Скачайте [ZIP проекта](https://github.com/ilnurcode/testpilot_1c/archive/refs/heads/main.zip) и распакуйте папку.
3. Дважды щёлкните `Run-TestPilot.bat`. Он соберёт и запустит весь стек, дождётся готовности и откроет браузер. Первый запуск скачает образы, Python-пакеты и Chromium и может занять несколько минут.
4. Вход: `expert` / `TestPilotLocal123!` (если нет пользовательского файла .env).
5. Для штатной остановки дважды щёлкните `Stop-TestPilot.bat`. PostgreSQL и файлы проекта сохраняются.

Нужен только Docker Desktop, не нужны локальные Python/Git/Ubuntu/команды. Подробности и устранение ошибок — [руководство для Windows](docs/windows.md). Продвинутый вариант через локальный Python/PowerShell — [windows-powershell.md](docs/windows-powershell.md).

## Презентационный режим

Проверенный запуск текущего этапа:

```bash
cd /workspace/testpilot_1c
./scripts/start-presentation.sh
```

В интерфейсе: **Обзор → Открыть демонстрацию → Подготовить проверки → Сформировать сценарии → подтвердить → Запустить на учебном стенде**. Выполняются реальные HTTP- и Playwright-проверки отдельного synthetic-приложения, сохраняются результаты и скриншоты. Внешняя нейросеть подключается через серверный TP_LLM_API_KEY; без ключа доступен явно маркированный учебный генератор. Реальная 1С не имитируется.

[Пошаговый сценарий показа, настройка API и границы](docs/presentation.md).

## Проекты и сохранённые загрузки

Откройте **«Проекты и файлы»**, создайте проект и загрузите разработку. Интерфейс сохраняет исходный файл, показывает историю версий, распознаёт дубликаты и предлагает предыдущую загрузку того же компонента. Можно выбрать другую базу или полный анализ. Файлы и отчёты остаются в PostgreSQL после обновления страницы и перезапуска приложений.

CFE/CF/EPF/ERF/DT и ZIP принимаются на хранение до 64 МиБ. Анализ сейчас доступен для нормализованного JSON до 8 МиБ или ZIP с `configuration.json` в корне. Бинарные файлы получают честный статус «Ожидает разбора»: их автоматическое извлечение, установка и запуск в 1С не выполняются. Для внешней нейросети подготовлен OpenAI-совместимый API-адаптер; фактический вызов требует серверного ключа.

Подробности, ограничения и API: [библиотека файлов](docs/uploads.md). Compose-сервис migrate применяет актуальные Alembic-ревизии. Не удаляйте PostgreSQL volume.

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

`docker compose restart` сохраняет PostgreSQL volume и результаты. Для остановки без удаления данных — `docker compose down` (без `-v`). Не удаляйте volume для «исправления» конфликтов импорта. Другой пакет с тем же номером версии получает 409; используйте новый номер версии. История и отчёты immutable; тесты удаляются логическим архивированием.

Параметры: `DATABASE_URL`, `TP_ANALYST_PASSWORD`, `TP_EXPERT_PASSWORD`, `TP_RUNNER_PASSWORD`, `IMPACT_URL`, `INTELLIGENCE_URL`; `WEB_URL` — только для smoke. Секреты не встроены в исходники, Dockerfile и инструкции. Не включайте `.env` в Git или Docker build context.

Подробнее: [архитектура](docs/architecture.md), [алгоритмы](docs/algorithms.md), [API/контракты](docs/api.md), [соответствие учебным материалам](docs/requirements-map.md), [будущая 1С](docs/1c-integration.md), [ограничения](docs/limitations.md), [фактические проверки](docs/validation.md).

## Первый шаг подключения реальной 1С

Целевой рабочий цикл с расширениями, OData и Playwright описан в [connected-workflow.md](docs/connected-workflow.md). Он требует следующего этапа разработки и проверки на предоставленном стенде; текущий runner для 1С ещё не реализован.

Добавлена read-only диагностика публикации:

```bash
# Задайте TP_1C_ODATA_URL и при необходимости TP_1C_WEB_URL в защищённой среде.
# Схемы переменных аутентификации — в .env.example. Значения в чат не передавайте.
.venv/bin/python -m testpilot.integration.probe
```

Команда выполняет GET `$metadata` и GET страницы веб-клиента, выводит список опубликованных EntitySet, различает 401/403/сетевую ошибку/неправильный XML. Не изменяет данные, не следует redirect с паролем и не выдаёт страницу входа за успешную аутентификацию. Проверка веб-страницы выполняется без переноса OData-пароля. Доступность `$metadata` не подтверждает права на данные, версию BSL/расширений или работу Playwright-тестов. Адаптер проверен на стандартном контрольном EDMX и моделируемых HTTP-ответах; реальная публикация пользователя пока не проверена.


Проверка новой библиотеки в браузере (Playwright и Chromium):

```bash
.venv/bin/python scripts/projects-browser-smoke.py
```

Она создаёт отдельный явно синтетический проект и проверяет загрузки, полный/дифференциальный анализ, скачивание исходных байтов, повтор загрузки, обновление страницы, поиск и мобильную ширину. Локальные скриншоты сохраняются в `.runtime/projects-desktop.png` и `.runtime/projects-mobile.png`.
