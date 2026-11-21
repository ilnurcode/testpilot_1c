# Изменения

Диапазон: 2026-11-16 — 2026-11-21 (историческая нумерация коммитов не является текущим счётчиком; дерево опубликовано в `origin/main` 2026-11-21):
`git push` выполнен 2026-11-21 (публикация, без переписывания истории и без `--force`).

## 2026-11-21 — публикация: полный suite на одноразовом PostgreSQL, статус в README

Публикация накопленной работы в `github.com/ilnurcode/testpilot_1c` (`origin/main`): per-user установщик и упаковка,
секреты подключения (DPAPI и portable AES-256-GCM с переносом ключа), typed assertions и исполнимость локального плана,
асинхронные прогоны с claim/lease/checkpoint и восстановлением, демо-путь в UI, отчёты и evidence, миграции `0009`/`0010`,
тесты. В README добавлен раздел «Статус: что проверено» — что подтверждено и чем, и что не проверялось.

Проверка перед публикацией: один полный `pytest -q` на одноразовом PostgreSQL 17-alpine (`--tmpfs`, случайные креды,
маркер `COMMENT ON DATABASE` = `TP_TEST_INSTANCE_TOKEN`, `DATABASE_URL` пуст, `TP_UI_BASE_URL` не задан).
Первый прогон: 805 passed, 1 failed, 6 skipped, exit 1 — `test_projects_list_has_no_n_plus_one` (2 → 4 SQL).
Первопричина: фоновый воркер прогонов опрашивает `ti_ai_suites` в своём потоке на том же engine, и его SQL попадал
в счётчик запросов; счётчик изолирован по потоку (`ai-run-worker`), к запросу страницы это отношения не имеет.
После правки упавший модуль трижды зелёный (21 passed), затем один полный повторный прогон:
**806 passed, 6 skipped, 497 warnings, exit 0, 1185 s**. UI-smoke в полном прогоне пропускается без `TP_UI_BASE_URL`.

Границы те же, что в README: реальная 1С и LLM не использовались, типовая конфигурация и импорт реального артефакта
не проверялись, чистая Windows VM и обновление между версиями не проверялись, portable-ключ на Windows через Docker
(mode 0777) не проверялся. Детали — раздел «Статус: что проверено» в [README.md](README.md).

## 2026-11-20 — B4: локальный план проверен на стенде, три разрыва исполнимости исправлены

- Свой одноразовый стенд (самостоятельный compose-проект `tp_b4_…`, PostgreSQL 17-alpine tmpfs, host-порт 18074, сервисы 18100/18101/18102, случайные креды; после — удалён). Путь OData-анализа: `POST /api/presentation` (10 затронутых объектов, synthetic) → `POST /api/v1/analyses/{id}/deliver` → `POST /api/v1/ai/suites` `provider:"local"` → 200, suite `927ded45…`: 3 предложения (playwright/odata/manual), 3 кейса, 3 обязательные typed-проверки (`connectivity/web_section_visible`, `metadata/metadata_entity`, `manual`), `expected` — boolean true, у business `source_ref` нет по построению. Шаги playwright только `click/wait/assert_section`; селекторы разрешаются в поданные метаданные: `Document_ЗаказКлиента` → затронутый `order`, раздел `Продажи` → full_name проекта. `contracts/ai-suggestion-1.0.schema.json` + `ai-assertion-1.0.schema.json` (jsonschema/referencing): 0 ошибок сохранённого плана; HTTP-ответ добавляет документированное `mutating` вне схемы.
- Исправлен разрыв «посторонний объект»: EntitySet выводился из префикса `object_id` с выдуманным запасным `Catalog_Номенклатура`, поэтому при затронутом `Документ.ЗаказКлиента` проверялся справочник Номенклатура (на стенде — ровно так). Теперь `_odata_entity` в `testpilot/ai/provider.py` выводит сущность из full_name/типа затронутого объекта, документ приоритетнее; без выводимой сущности OData-предложение не строится и добавляется warning.
- Исправлен разрыв «генерация падает вместо отказа»: full_name с пробелом (`ВнешняяОбработка.Учебная обработка` после `.epf`) и очень длинное имя давали pydantic `ValidationError` → HTTP 500. Теперь имя EntitySet валидируется (`[\w-]+`), а title/rationale/вопрос обрезаются (`_clip`, Text ≤ 300).
- Исправлен разрыв «approve проходит, прогон падает в error»: демо-исполнитель принимал любой план синтетического пакета `presentation-sales` (только identity) и на typed-плане отдавал `0 passed, 0 failed, 2 error, 1 skipped` с сообщением о браузерном шаге. `_demo_plan_shape_error` в `testpilot/ai/api.py` требует `quantity/price/discount/expected_total` у утверждённых автоматических кейсов; теперь `run-demo` отвечает 409 с причиной, а typed-план исполняется в 1С через `run-1c`. `_is_demo_plan` оставлен прежним, чтобы `/rerun` по-прежнему отклонял синтетический план.
- Демонстрационный цикл (provider `demo`) на том же стенде: approve 200 → run-demo 200, `2 passed / 2 failed / 0 error / 0 skipped`, executor `synthetic_sales_http_and_playwright`; expected/actual (180/180, 900/1000) в MD и CSV (3328/1739 B).
- Регрессы: новый `tests/test_generation_schema.py` (схема, происхождение EntitySet, отсутствие выдуманной сущности, пробел/длина full_name, гейт демо-исполнителя). Прогон: `pytest tests/test_local_plan_steps.py tests/test_generation_schema.py tests/test_ai_assertions.py tests/test_local_plan_assertions.py -q` → **42 passed, exit 0**.
- Proof `C:/Users/Ilnur/AppData/Local/Temp/tp-b4-stand-20261120/proof.json` (+ report_md.txt, report_csv.txt). Реальная 1С/LLM не использовались; `run-1c` на стенде недостижим (у синтетического проекта нет подключения), остаётся D1.

## 2026-11-20 — финальные прогоны: 799 passed, C2 per-user DONE, portable key restore, 5 дефектов ревью, пересборка a3-fin3

- Полный suite, три последовательных прогона (каждый один раз): 787 passed/0 failed → после независимого ревью 798 passed/1 failed (`test_login_failure_screenshot.py::test_run_scenario_keeps_screenshot_when_login_fails`, пустой `pre_submit_screenshot`) → после исправления **799 passed, 6 skipped, 0 failed, errors 0, exit 0, 459.07s**. 6 skip — `tests/test_ui_smoke.py` (`TP_UI_BASE_URL` не задавался). Окружение: одноразовый PostgreSQL 17-alpine (tmpfs, loopback-порт, маркер `COMMENT ON DATABASE`), `TP_ALLOW_LOCAL_TARGETS=1`, `DATABASE_URL` пуст. Proof `C:/Users/Ilnur/AppData/Local/Temp/tp-fullsuite-ib060zbz/proof.json` (+ два прошлых proof рядом). 787/798 — история прогонов.
- C2 DONE (local, текущая версия): установка per-user без elevation — `installer/testpilot.iss`: `PrivilegesRequired=lowest`, `DefaultDirName={localappdata}\Programs\TestPilot1C`, ярлыки `{userprograms}`/`{userdesktop}`, uninstall-ключ в HKCU (секции `[Registry]` нет). install `/VERYSILENT` exit 0, 100/100 файлов sha256 = manifest; uninstall `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` exit 0 — каталог/ярлыки/HKCU-ключ/процессы `unins*` отсутствуют, HKLM до/после идентичен; прежние глобальные ярлыки `C:\ProgramData\...\Start Menu\Programs\TestPilot 1C` и `C:\Users\Public\Desktop\TestPilot 1C.lnk` удалены без elevation; остался мёртвый HKLM-ключ `HKLM\...\Uninstall\{6E2F5B7C-…}_is1` (требует администратора, `UninstallString` указывает на удалённый каталог). Обновление поверх: подменённый `pyproject.toml` восстановлен (100/100), одна запись в списке программ, ярлыки без задвоений. Proof `installer/proofs/installer-per-user-2026-11-20.json`. Ограничения: нет чистой VM, нет обновления между разными версиями, нет миграций БД при обновлении, GUI-мастер и упаковка `TestPilot.exe` не проверялись.
- A4 portable key: перенос и restore проверены в POSIX-контейнере — backup, второй чистый экземпляр, отдельно перенесённый key file, restore; пароль расшифрован и совпал; без ключа — понятный отказ без изменения данных; неверный ключ — fail-closed, ciphertext в БД не изменился; в SQL-дампе и staged-пакете нет ключа и пароля. Guard: `store_connection_password(..., rekey_confirm)`, `RekeyRequired` → 409 с текстом про backup и `rekey_confirm=true`; при отказе `auth_secret` байт-в-байт прежний, при подтверждении 200 и новое значение читается; недоступный ключ — 503. Тесты 40 passed (test_secrets + test_secret_storage + test_projects, exit 0); `docs/security.md` дополнен разделом о portable key. A4 остаётся PARTIAL/BLOCKED: Windows-хост не может использовать portable через bind-mount (mode 0777).
- Исправлены пять дефектов независимого ревью: сбой шага веб-клиента больше не выдаётся за бизнес-«Расхождение» (`error`, `business_checked=false`); `business_success` агрегируется в `execute_claim`/`_demo_finish`; `heartbeat` повторяет с backoff вместо тихого `return`, факт потери продления фиксируется; снимок формы не наследуется между кейсами, но сохраняется при падении входа в том же запуске (`try/finally` вокруг `handle_login`); «Прогнать снова» для demo-плана отклоняется 4xx и скрыто в UI. Affected 124 passed/exit 0; цикл `test_login_failure_screenshot` + `test_evidence_on_error` + `test_ai_assertions` + `test_1c_webclient` → 42 passed/exit 0.
- Финальная пересборка из текущего дерева (proof `C:/Users/Ilnur/AppData/Local/Temp/a3-fin3-proof.json`): diff sha256 `1f39207d22f110fc7a23dc135c9cb6f34c6e34c04ee2037b0f471533f15dfdaa` (578 293 B), 122 porcelain-строк/125 путей; stage/verify exit 0; manifest 0.1.0, 100 файлов, 828 046 B, sha256 `e0da25c5b171b545527497e74a23110db6ec5875d76e4ac0f70c72f8b1c028c7`; staged==live 52 пути/0 mismatches (`webclient.py`, `executor.py`, `worker.py`, `ai/api.py`, `impact/projects.py` включены); `installer/testpilot.iss` и `docs/` не входят в пакет по allowlist, `.iss` для ISCC совпал с live байт-в-байт; artifact `TestPilot_Setup.exe` sha256 `b2e72138eca0eab0f4789573a83ca56111b8c59dfd9ce047f1fbc2d4748a8c0c`, 2 276 334 B, 0.1.0; установщик не запускался. A3 остаётся PARTIAL (нет чистой VM, нет обновления между версиями, нет миграций БД при обновлении, GUI-мастер и `TestPilot.exe` не проверялись); прежние пересборки — история.
- Процессный факт: независимую проверку выполнял отдельный агент без контекста (не владелец продукта); это не заменяет D2.

## 2026-11-20 — финальная пересборка пакета/установщика из текущего дерева (a3-fin2, историческая)

- A3/C2 выполнены: proof `C:/Users/Ilnur/AppData/Local/Temp/a3-fin2-proof.json`; HEAD `ebe4a436…`; diff-рецепт `git status --porcelain|sort; echo ===DIFF===; git diff` → sha256 `7459930051316871ef4bf2034806645735fcfbf451f860a62525092e70d2ca34` (545 201 B), 121 porcelain-строк / 124 уникальных пути; stage exit 0, verify exit 0; manifest 0.1.0, file_count 100, total_bytes 821454, manifest sha256 `26748c44f36bb0a51bb69793be8bd5cf744359064226f26a98a9f4ead35e9c5c`; staged==live 52 сверенных пути, 0 mismatches — обязательные app.js/ai.js/workspace.js/executor.py/ai/api.py/integration/webclient.py/audit_api.py/presentation.py/.env.example присутствуют и байт-в-байт равны live; `README.md` в пакет не входит по allowlist staging (дизайн, не порча); ISCC 6.7.3 exit 0 (warning x64→x64os), `TestPilot_Setup.exe` sha256 `0df8cf064e5f249fb362a20dfe30119a51cb368a982b0ccefc2b0b5b2f4983ee`, 2 275 032 B, File/Product 0.1.0; установщик в этом прогоне НЕ запускался; launcher trust unverified → `TestPilot.exe` не поставляется.
- На момент a3-fin2 C2 был PARTIAL: нет clean Windows VM, нет обновления с предыдущей версии, нет установки пост-правок, остатки прошлого uninstaller cleanup требуют elevation. Затем C2 закрыт per-user установкой без elevation (раздел выше), A3 остаётся PARTIAL. Предыдущий refresh `fc2892…` (`%TEMP%/a3-final-proof.V5pU6H.json`, 807275 B) — исторический.

## 2026-11-20 — C1 UI demo path DONE (local/Docker browser E2E)

- C1 переведён в DONE на локальном стенде: обещанный UI-путь реализован (`workspace.js`, `ai.js`, `presentation.py`, удалён мёртвый `overview()` в `app.js`, `README.md:177`) и пройден живым стендом headless Chromium: `/api/presentation` 200 (project `aea71628-92a2-4eb1-8b9f-bc16e8a3b293`, analysis `a4ad6715-…`, 10 объектов) → `/ai/suites` 200 (2 сценария) → approve 200 → `run-demo` 200 (2 passed/2 failed/0 error/0 skipped, 4 карточки); повтор даёт «Проект уже существовал» с тем же project_id; md 3326 Б/csv 1737 Б байт-в-байт со скачанными через UI; 409/403 показаны текстом, 500 карточкой без падения экрана; JS-исключений 0 (3 `Failed to load resource` — намеренные 409/403/500). Детали изменений — в разделе ниже.
- `TP_UI_BASE_URL=<live> TP_UI_REQUIRED=1 pytest tests/test_ui_smoke.py -q` → 6 passed (в общем полном прогоне UI-smoke был пропущен: `TP_UI_BASE_URL` не задавался). Proof `C:/Users/Ilnur/AppData/Local/Temp/tpui-7a855c80/proof.json` (+ `shots/01-overview.png … 12-upstream-error.png`, `report-*.md|csv`, логи).
- Границы: реальная 1С и LLM не использовались, демо-путь синтетический; это локальный/Docker browser E2E, не release acceptance. A3 (installer/clean VM/upgrade), A4 Docker portable key, B1, B4, D1/D2 не менялись. Правки UI/`presentation.py` сделаны после C2/A3 stage и вошли в финальную пересборку из текущего дерева (раздел выше).

## 2026-11-20 — первый полный pytest suite: 787 passed (исторический)

- Демонстрация из UI доведена до обещанного README/доками пути: `workspace.js` — кнопка «Открыть демонстрацию» (`#presentationStart`) и карточка результата (`openPresentation`/`presentationFailure`: «Проект создан»/«Проект уже существовал», project_id, «Подготовить проверки →», «История файлов проекта →», сообщения для 401/403/409/5xx/сети); `ai.js` — провайдер `demo` «Учебные правила — без нейросети» только для synthetic `presentation-sales` (`demoSuite`), для таких планов вместо `run-1c` кнопка «Запустить на учебном стенде» → `run-demo` (`runDemoSuite`: 409/403/сеть — сообщение в `#suiteFeedback`, дальше `renderSuite` с counts и ссылками md/csv); `testpilot/web/presentation.py` отдаёт аддитивное `created_project`; мёртвый `overview()` (`static/app.js`, единственный клиентский вызов `POST /api/demo`) удалён, серверный `/api/demo` не тронут; `README.md:177` приведён к фактическим подписям. Live-стенд свой (`tpui-7a855c80`, tmpfs PG, loopback 60415/60416/60417, head 0010): `/api/presentation` 200 → suites 200 → approve 200 → run-demo 200 (counts 2 passed/2 failed/0 error/0 skipped), MD 3326 Б и CSV 1737 Б (200, байт-в-байт равны скачанным), 409/403/500 показаны сообщением без падения экрана, JS-исключений 0; proof `%TEMP%\tpui-7a855c80\proof.json` + shots.
- Первый прогон полного suite (исторический): `.venv/Scripts/python.exe -m pytest -q` → exit 0, `787 passed, 6 skipped, 479 warnings in 445.10s`. 6 skip — `tests/test_ui_smoke.py` (маркер `ui`; `TP_UI_BASE_URL` не задавался). Стенд — одноразовый PostgreSQL 17-alpine (tmpfs, loopback-порт, маркер `COMMENT ON DATABASE`, `TP_ALLOW_LOCAL_TARGETS=1`, `DATABASE_URL` пуст). Proof `C:/Users/Ilnur/AppData/Local/Temp/tp-fullsuite-f407fpfn/proof.json`. Реальная 1С и LLM не использовались. Прежние 83-focused и 141 affected остаются историческими частичными прогонами; финальный результат — 799 passed (раздел выше).
- `testpilot/ai/executor.py`: восстановлена запись evidence — `_usable_shot`, `_record_evidence`, `evidence_provenance ∈ {step, pre_submit, none}`, `pre_submit_screenshot_bytes`, `screenshot_unavailable`/`_reason`; терминальный статус снова берётся из отчёта, `failed` не превращается в `error`. До правки CSV-колонки «Источник evidence»/«Причина отсутствия снимка» и строка Evidence в MD были пустыми при живом `evidence_id`.
- `testpilot/integration/webclient.py`: в обработчике ошибок `page`/`context`/`browser` предварительно привязаны к `None`, снимок и закрытие под защитой; ранее реальная ошибка подменялась `UnboundLocalError`.
- `testpilot/ai/api.py`: `_profile_for_suite` отдаёт 422 вместо необработанного 500 при логине без пароля и при неверной конфигурации подключения; добавлен `test_run_1c_login_without_password_is_422_not_500`; убран затеняющий локальный импорт `render_run_report`, из-за которого monkeypatch в тестах отчёта не действовал.
- `testpilot/web/audit_api.py`: отпечаток одиночного файла — sha256 байтов (`_file_fingerprint`), а не нормализованного текста; CRLF-файл больше не даёт другой хеш. Совпадение проверено: API = `sha256sum` = `Get-FileHash` (`e1c69f…`, `76f4e2…`).
- `.env.example`: добавлены `TP_INSTANCE_TOKEN`, `TP_SECRET_PROTECTOR`, `TP_PORTABLE_SECRET_KEY_FILE`, `TP_TARGET_ALLOWLIST`, `TP_ALLOW_LOCAL_TARGETS` (без секретов).
- Тестовый дрейф приведён к реальному контракту без ослабления проверок: CSV — строка на case; `/rerun` — `202`+очередь; `detail` структурный `{message, code}`; `should_cancel` в фейках; typed assertions обязательны; allowlist в тестах подключения.
- Ограничения: UI-smoke в прогоне пропущен (`TP_UI_BASE_URL` не задавался), реальная 1С/LLM не использовались, Docker portable key (A4) остаётся BLOCKED; installer/clean VM/upgrade не закрыты. Правки этих файлов сделаны после C2/A3 stage и вошли в финальную пересборку из текущего дерева (раздел выше); установщик не запускался.
- Открытый дефект C1 (на тот момент не исправлен; закрыт — раздел «C1 UI demo path DONE» выше): README (`26`, `177`), `docs/presentation.md` (`28`, `30`, `32`), `docs/windows.md` (`76`, `82`), `docs/windows-powershell.md` (`95`, `99`), `docs/windows-wsl.md` (`127`, `131`) описывают кнопки «Открыть демонстрацию» и «Запустить на учебном стенде», которых в UI нет; серверные эндпоинты (`POST /api/presentation`, `provider:"demo"`, `POST .../run-demo`) работают; `app.js::overview()` — мёртвый код, привязанный к `POST /api/demo`. Обвязка UI идёт отдельно.

## 2026-11-20 — t8 combined, C1 continuation, ai.js fix, C2 refresh

- Advisor t8 на real PG: combined affected сначала 139 passed/2 failed; минимально исправлены `tests/test_ai_async.py` (явная configured target до legacy typed gate) и `tests/test_ai.py` (CSV выполненные+невыполненные плановые cases); focused 2 passed; финальный combined 141 passed, 137 warnings, 151.90s, exit 0. Это 14 файлов + 12 consumer nodeIDs, но не full suite; t7 gate покрыт; machine todo не повышен.
- B3 fixture/local DONE: fixed «негативный исход выдан за успех» при уходе сеанса на 401: `assert_section` бросал AssertionError, expected=false давал passed. В `testpilot/integration/webclient.py` добавлены `WORKSPACE_ROOT_SCRIPT` + `workspace_root_present(page)`; без рабочей области assertion `status=error` вместо value=false, evaluate_assertions даёт overall `error`. Регресс `tests/test_1c_webclient.py::test_negative_section_assertion_requires_live_workspace`. Own loopback без Docker/БД/1С/LLM: один login/context, logins=1; true→passed, false→failed с expected/actual; 404/401/403/timeout→error; два прогона login 1/2 детектируют смешение; narrow 1 passed, affected `test_1c_webclient.py + test_ai_assertions.py` 27 passed при `TP_ALLOW_LOCAL_TARGETS=1` (без него 7 падений — среда). Proof `%TEMP%/tp-b3-proof-1b65fa3c/proof.json`, pre-fix `%TEMP%/tp-b3-proof-68eb0923/proof.json`. Ограничения: реальная 1С не использовалась, cookie при assertion не читались, OData/PG не поднимались; B3 real-1C остаётся D1. B3 edit после последнего A3 repack `fc2892…` вошёл в финальную пересборку (раздел выше); установщик не запускался.
- B2 demo path lock fix: `run_demo` больше не держит `session` на время браузера; `_demo_claim` — короткая транзакция `with_for_update`, `_demo_finish` под fence попытки; `execute(...)` вне транзакции, heartbeat продлевает lease короткими транзакциями. Affected argv `.venv/Scripts/python.exe -m pytest tests/test_ai_demo_run.py tests/test_ai_async.py tests/test_run_modes_local.py tests/test_run_recovery.py -q -p no:cacheprovider` → 21 passed, 0 failed, 44 warnings, exit 0, 27.95s. Proof `%TEMP%/tp-b2lock-11201758c23351/proof_before.json`/`proof_after.json`: idle in transaction 0 (было 1), `FOR UPDATE NOWAIT` granted (было lock not available), sampler 0/16 (было 15/17), parallel cancel 2.318s→0.247s. Новые тесты: `test_demo_run_holds_no_transaction_or_suite_lock_during_long_step`, `test_parallel_demo_run_is_rejected_by_short_claim_without_duplicates`, `test_demo_failure_is_terminal_and_does_not_leave_lease`, `test_expired_demo_lease_is_reaped_and_late_result_is_fenced`. Affected t8 141 passed остаётся affected, не full suite. B2 PARTIAL (affected green); реальная 1С/LLM — D1. B2/B3 edits после A3 repack `fc2892…` вошли в финальную пересборку пакета/установщика (раздел выше); установщик не запускался.
- Interface t5: свой nonce `7419c40334fa877d`, container `43fa9e6e78d0`, head 0010; фактический app+PG+localhost fixture, 13/13 семантических проверок: queued→running→cancel_requested→cancelled, fixture_gets=1/no next GET, reload cancelled; HTTP500→case error+manual skipped counts 0/0/1/1 business null; остановлен свой intelligence: UI 503 unknown→реальный TTL 300→restart на том же PG→interrupted, no duplicate GET=1, reload/history; MD/CSV совпали с сохранёнными статусами/assertions; JS errors 0. UI recovery прошёл без предшествующего checkpoint; отдельное API-доказательство checkpoint не приписывается UI.
- После continuation Interface исправил ai.js labels cancelled→«Отменён»/interrupted→«Прерван» и merge `{...fullSuite,...state}`; DB-free Chromium static proof на настоящих static/in-memory API прошёл (queued overrides stale approved; running/cancel_requested overrides stale queued; cancelled label русский; JS errors 0; node --check ok). Interrupted-case label отдельно не проверен. Divergence: backend combined/13 semantic были ДО ai.js fix, static proof ПОСЛЕ; full app post-edit не повторялся.
- C2 свежий TEMP source-package после ai.js/docs: `--dest`/`--verify` 0/0, 102 файла, 0.1.0, staged head 0010, tree `ea75d6e8cb1d4c1f001266f0ea5aec7dbba66c3a1da6d966d07eda2b95c65615`, manifest `8017f98b806f4b6097b703a2fdc27677006417f18e81008218c2667f351f577e`; staged==live для ai.js/api.py/schema_state.py, import testpilot/ai.api/schema_state/secrets/target_policy внутри stage, editable_finder=false, 0 нарушений allowlist; свой TEMP удалён. Это состав/import, НЕ installer/clean VM; старые hashes 7daadf/55326f — исторические до ai.js.
- A3 final refresh proof `%TEMP%/a3-final-proof.V5pU6H.json`: HEAD `ebe4a436…`, working_tree_diff_sha256=`09e256c5168b0de38d3d3c2515b68bf52db2b172663edde4058fb52d80163d2b` (рецепт `git status --porcelain | sort; echo ===DIFF===; git diff` → sha256; индекс не менялся, 110 changed paths); stage/verify exit 0; manifest 0.1.0, file_count 100, total_bytes 807275 (на диске 102: manifest.json/version.iss не считаются); staged==live 48 changed package paths, 0 mismatches; `compose.portable-secrets.yaml.example`/`tests/test_compose_portable_secrets.py` исключены положительным allowlist (root ALLOWED_FILES + ALLOWED_TREES testpilot/migrations/scripts) — дизайн, не дефект; ISCC 6.7.3 exit 0 (warning x64→x64os), `TestPilot_Setup.exe` SHA256 `fc289205ed128d9df2231385bfa5d62191cb5f81bd954401eddd64becee38e98`, 2271941 B, File/Product version 0.1.0, ProductName `TestPilot 1C`; installer в refresh НЕ запускался; launcher trust unverified → `TestPilot.exe` не поставляется. A3 PARTIAL: clean VM/upgrade нет, post-A4 install нет, previous uninstaller cleanup residue remains; old 74f8/44ca/31b8/c5fa evidence исторические. B3 edit после последнего A3 repack `fc2892…` вошёл в финальную пересборку из текущего дерева (раздел выше); установщик не запускался.
- Cleanup: Advisor все 3 ownnonce PG удалены только recorded IDs+marker, tempdirs gone; Interface own container/stack/fixture/ports/temp cleaned, proofs preserved. Owner DB/env/1C/LLM/migrations/git не трогались; новых installs нет. Docker Desktop permission для isolated checks уже дано; повторно не запрашивать. B1 real artifact, D1 real 1C/actions, real LLM, clean Windows installer/VM remain BLOCKED; на тот момент full suite/full acceptance не заявлялись (отдельный полный прогон — раздел «полный pytest suite» выше).
- A4: `cryptography==50.0.2` объявлена runtime-зависимостью; portable `portable-aesgcm-v1` использует AES-256-GCM, external key file и project-bound AAD. Windows DPAPI остаётся default/backward-readable; отсутствующий/неверный/повреждённый ключ и подмена ciphertext дают fail-closed, а connection/profile пути — controlled 503. `jsonschema==4.26.0`/`referencing==0.37.0` — зависимости contract validation; 8 schema files и cross-document `$ref` проверены. Affected suite на own disposable PostgreSQL: 52 passed, 87 warnings, exit 0. Это не доказательство Docker secret-mount deployment; base compose semantics не менялись.
- A4 Docker portable key: добавлен opt-in `compose.portable-secrets.yaml.example` + compose guard test; native Docker Desktop run показал file-backed key внутри container mode 0777, Compose проигнорировал requested mode; portable path → controlled HTTP 503, no plaintext fallback. Unsafe 0777 exception отклонён/откачен; strict unit/config `unsafe_0777_rejected=1`, exact 4 passed/1 warning. Compose example НЕ working deployment; external-key backup/restore не запускался; A4 PARTIAL/BLOCKED для Docker portable key. A4 compose/test changes после A3 refresh 74f8…; current package/installer proof требует нового refresh, не запускать.

## 2026-11-19 — B2/B3, C1 normal proof, C2 staging и финальный статус

- B2/B3: typed assertion models/schema, same-context action/assert, step/case callbacks, claim/lease/reaper/checkpoint, cooldown target+principal; focused source-freeze command 83 passed, 1 PG-dependent deselected. Это не total suite и не включает t7.
- t7 divergence: после 83-pass baseline Advisor изменил `_validate_run_assertions` (+422 для absent/empty cases odata/playwright) и добавил один targeted test; он прошёл 1/1. Кандидатный guard существует, но baseline/frozen state не полный green; нужен новый допуск на review diff + full affected set, без auto-undo.
- C1 actual normal (own nonce 9036…, project 7681ec6f…, suite 693fa040…): ZIP import→audit 8/12→impact 4→local plan→approve→preflight 200→202/poll/completed→MD/CSV; typed metadata true→true/manual skipped, counts 1/0/0/1, не business pass. Cancel/error/interrupted/restart не запускались (Docker CLI blocked); harness KeyError optional business_success после отчётов — не backend.
- C2 source staging (refreshed after tiny fix): `--dest`/`--verify` 0/0, 102 files, 0.1.0, head 0010, tree SHA 7daadf…, manifest SHA 55326f…, staged import, editable finder false, 0 allowlist violations. Это не Windows installer/clean VM; ISCC и built image отсутствуют; `installer/staging` не обновлялся.
- A3 CLI actual-surface remains historical: init 0/repeat no migration/incompatible exit 3/cleanup 0, existing image + RO mount; built-image clean install не проверен. A4 historical pre-A4: DPAPI/0010/network gates, portable/Docker secret storage BLOCKED at that time (cryptography не обнаружена в проверенных интерпретаторах); superseded by A4 source/versions above.
- Blocked gates: B1 real artifact/inventory; D1 real 1C grant/actions; real LLM key/consent; clean Windows install; full D2 release. Machine todo 3/13; final docs statuses are PARTIAL/BLOCKED, не «всё готово».
- JSON schema file `contracts/ai-assertion-1.0.schema.json` присутствует; на дату той записи `jsonschema/referencing` отсутствовали, теперь A4 source/versions выше дополняют это, library validation ранее NOT RUN.

## 2026-11-18 — A3 (запуск и миграции) и A4 (частично)

- Запуск больше не меняет схему: сервис `migrate` доступен только явно (`--profile init`), у приложений убран `depends_on: migrate`. Обычный `start.ps1`/`start-local.sh` схему только проверяет.
- Явные действия: `scripts/init.ps1` (только для пустой схемы), `scripts/update.ps1 -Confirmed` (только вперёд, без downgrade/stamp), `scripts/first-run.ps1` (единый вход установка→схема→запуск), `scripts/start-docker.ps1` (Docker: `up --wait` + проверка головы миграций в образе + фактический health трёх сервисов + квитанция). Для Docker-режима Python/venv/`.env` не нужны.
- Коды возврата `first-run.ps1`: 2 — нет предпосылок (Docker CLI, права записи в каталог данных) или отсутствует дочерний скрипт (профиль не подменяется), 3 — схема pending/ahead (нужен backup и `update.ps1 -Confirmed -Docker`), 1 — непредвиденная ошибка.
- `testpilot/schema_state.py`: состояния `ok/pending/ahead/uninitialized/unknown` по голове миграций из `ScriptDirectory` (без hardcode ревизии). `/health` отдаёт 503 с понятным текстом при pending/ahead/недоступной БД, без DSN и трассировки. Каталог данных и venv могут быть вне каталога программы (`TP_DATA_DIR`, `TP_VENV`), `.env`/`.runtime` следуют за ним.
- `launcher.py`: браузер открывается только когда все три сервиса ответили `status=ok`; таймаут/сбой запуска — ненулевой код и без браузера; таймаут настраивается `TP_START_TIMEOUT_SECONDS`.
- A4 (Windows, частично): новый пароль подключения не хранится открытым текстом — DPAPI пользователя (`envelope dpapi-user-v1:`), миграция `0010` добавляет nullable `auth_secret`, `auth_secret_scheme`, `auth_secret_updated_at`; ORM-поля только в `ProjectRow`. Runtime использует ORM без introspection; старая схема даёт `schema_not_ready`. Legacy-текст не отдаётся для использования: `scripts/secrets_key.py --migrate-legacy --confirmed` шифрует после проверенного roundtrip и только затем очищает поле; старые значения не удаляются.
- A4 политика целей: `TP_TARGET_ALLOWLIST` (строгий `host[:port]`/`[v6]:port`, запись без порта — только 80/443) и `TP_ALLOW_LOCAL_TARGETS`; отказ до отправки на не http(s), userinfo, хост вне allowlist; cross-origin редирект и пересылка credentials отклоняются; в сообщениях только безопасный origin.
- A4 **не завершён**: portable recovery с отдельно переносимым ключом и хранение пароля в Docker/Linux остаются BLOCKED — в окружении нет vetted cross-platform crypto, установка `cryptography` ожидает решения владельца. DPAPI привязан к профилю Windows и перенос на другую машину не покрывает.
- C1: терминальные статусы прогона включают `cancelled` и `interrupted`, поэтому последний прогон не скрывает отмену/сбой за старым `completed`; `has_password` в списке проектов считается из ORM без N+1.
- Локальные проверки (без запуска рабочего стека): `tests/test_startup_contract.py` + `tests/test_scripts_syntax.py` — 52 passed; `tests/test_target_policy.py` + `tests/test_secrets.py` — 17 passed; на одноразовом PostgreSQL — `tests/test_secret_storage.py` + `tests/test_projects.py` — 26 passed (upgrade 0009→0010, DPAPI roundtrip, legacy-отказ, чужой протектор без мутации).
- Не выполнялось: миграция `0010` на рабочей БД, живой Docker-прогон, A1 full-app smoke, реальные 1С/LLM/ванесса.

## Возможности

- Журнал аудитов вставленного кода: сохранение скана, карточка записи, общий экспорт отчёта md/csv.
- Правила сканера SEC010–SEC012 (безопасный режим, склейка текста запроса, COM-объект) и честный статус «проверять нечего» для пустого OData.
- Правила SEC013 (маскирование значения) и SEC014 (подключение внешней компоненты).
- Постраничная выдача замечаний аудита для сканов и загрузок (`issues_url`, `total`, `issues_kept`), карточка без деталей при превышении среза.
- Настраиваемый предел детализации `TP_AUDIT_MAX_ISSUES` (100…20000) с полным счётчиком `total_issues`.
- Удаление одной записи журнала аудита и каскадное удаление проекта — оба с подтверждением по идентификатору.
- Список проектов: поиск, фильтр «только с расхождениями», пагинация, последний прогон, колонка «Изменения».
- История прогонов проверок по проекту, отчёт о прогоне в md/csv, повторный прогон сохранённого плана.
- Строгий read-only режим прогона и явное согласие на изменяющие шаги; запрет `open_url` вне origin веб-клиента.
- Скрипт приёмки одной командой (`acceptance.sh`/`.ps1`) с самоочисткой созданных проекта и скана (`--keep` отключает).
- Схема API закрыта по умолчанию (`TP_OPENAPI_PUBLIC` открывает локально).
- Строгая проверка числовых и URL-настроек на старте; настройки LLM по умолчанию не пишут `.env`.
- Read-only зонд формы входа `POST /api/v1/ai/connection/probe-login`: режим GET-only и opt-in `boot` (same-origin non-GET разрешены, чужой origin блокируется до отправки), поиск формы по всем фреймам, `credentials_sent=false` и счётчики `non_get_requests`/`blocked_requests`/`blocked_non_get`; opt-in шаг приёмки `--probe-login`/`-ProbeLogin`.
- Вход в 1С по подтверждённым зондом id (`#authWindow_basic_login`, `#authWindow_basic_password`, `#authWindow_basic_okButton`) с поиском формы по фреймам и ожиданием её появления `TP_1C_LOGIN_WAIT_SECONDS` (5…120, по умолчанию 25).
- Первый успешный живой прогон на DemoTrd (2026-11-17): вход под «Администратором» и навигация в «Продажи» — 1 passed, evidence-скриншот рабочего стола, 98.26 с, read-only (`allow_mutations=false`).
- Диагностика неудачного входа в 1С: одна попытка без ретраев, распознавание сообщений платформы («Неверный пароль», «Блокировка», «Превышено количество попыток», «Пользователь не найден»), компактный дамп формы в ошибке (только tag/type/id/name, без значений и текстов), настраиваемый `TP_1C_LOGIN_GRACE_SECONDS` (2…60, по умолчанию 10).

## Исправления

- Наблюдение: 1С после клика показывает состояние обработки; увеличено ожидание (grace 30 с) и добавлено распознавание `processing`. Не подтверждено повторным живым прогоном.
- Навигация веб-клиента: маппинг loopback и откат адреса только для connection-level ошибок.
- Зонд формы входа больше не выдаёт ложный успех: ошибки навигации/ожидания дают 503, origin проверяется после навигации, форма ищется по фреймам с диагностикой, а решение принимается по ожиданию `TP_1C_LOGIN_WAIT_SECONDS` вместо проверки через 300 мс.
- Честные поля `/health` про исполнителя 1С; честные статусы бинарных контейнеров.
- Отпечаток скана и граница хранения фрагментов замечаний.
- Полный каскад удаления проекта (загрузки, анализы, suite, прогоны, evidence).
- Отчёт о прогоне по каждому кейсу; разделение SEC002 и SEC011.
- Восстановление guard'ов `api.py` и сборка валидного `provider.py`; каталог тестов для свежего ZIP-анализа.
- Приёмка: голова `alembic` без привязки к номеру ревизии, обязательный UI-smoke, понятный отказ без ключа ИИ.
- Обзор не падал на страничной форме списка проектов; UI не выходит за границу среза деталей.
- Порядок шагов навигации в локальном плане (click → wait → assert_section) — не связано с падением входа:
  причина была в том, что `handle_login` молча пропускал форму логина. Теперь вход при неудаче падает с явной ошибкой.
- Уникальные `operationId` в OpenAPI: шлюз разделён на четыре маршрута (GET/POST/PUT/DELETE) с явными `operation_id`, генераторы клиентов больше не получают дубли.
- Вход в 1С больше не проглатывает исключения: текст причины сохраняется в сообщении шага, неудачная попытка не повторяется.
- Удалены мёртвые вычисления, дубль импорта и мёртвая переменная по pyflakes.

## Тесты и стражи

- Страж полноты `docs/api.md`: каждый маршрут OpenAPI должен быть описан.
- Страж полноты `.env.example`: все читаемые переменные окружения задокументированы с комментарием.
- Страж соответствия правил сканера и `docs/rules.md`.
- Автоматический UI-smoke разделов в приёмке (обязателен; в обычном прогоне — skip).
- Регресс на запрет внешнего `open_url`; тест сохранности evidence между сеансами.
- Бюджет времени сканера на объёмном каталоге (200 файлов × 100 строк).
- Страж уникальности `operationId` в OpenAPI (и отсутствия предупреждений FastAPI о дублях).
- Страж допустимых методов шлюза (GET/POST/PUT/DELETE принимаются, PATCH/OPTIONS/TRACE/HEAD → 405) и проброса тела, пути и query в апстрим на заглушке `httpx`.
- Страж синтаксиса скриптов: `bash -n` для `.sh` и `Parser::ParseFile` для `.ps1` (на Linux без PowerShell — skip).
- Страж относительных ссылок в документации (24 md-файла, 56 ссылок).
- Строгие маркеры pytest: `--strict-markers` в `addopts` и запрет `-q` в `addopts` (иначе приёмка теряет строку «N passed»).
- Тесты входа на HTML-моках: пустой пароль → ровно одна попытка, текст платформы в ошибке, дамп без placeholder/текста кнопок/значений, медленный рабочий стол в пределах grace.
- Тест порядка шагов локального плана (навигация → ожидание → проверка) и адресов шлюза из окружения (хост и compose).

## Документация

- `docs/handoff.md` — состояние, проверенное живьём, ограничения, следующие шаги.
- `docs/api.md`, `docs/rules.md`, `docs/security.md`, `docs/acceptance.md` — маршруты, правила, постура, чек-лист.
- `docs/incident-2026-11-17.md`, `docs/forensic-playbook.md`, `docs/recreate-demo.md` — инцидент, разбор копии диска, пересоздание демо.
- Ранбук пересоздания демо: шаг `POST /api/v1/analyses/{analysis_id}/deliver` перед `POST /ai/suites` (без доставки пакета сервис intelligence отвечает 404).
- Приёмка и README предупреждают: не запускать `docker compose down -v` на стенде с данными.

## CI и инструменты

- CI: pyflakes-страж на `undefined name`, прогон тестов на push/pull_request, джоба полной приёмки на собранном стеке в изолированном проекте `testpilot-ci`.
- `scripts/backup.sh`/`.ps1` — `pg_dump` в `.runtime/backups`; `restore.sh`/`.ps1` — черновик восстановления только в пустую схему.
- `scripts/compose.sh`/`compose.ps1` — обёртки, через которые ходят `acceptance`, `backup`, `restore`, `start-presentation`:
  `down -v`, `volume rm` и `volume prune` рабочего проекта получают отказ (код 3), `CI=1` запрет не снимает;
  прямой вызов `docker compose` в обход обёртки она не перехватывает.
- `scripts/forensic-copy.ps1` — консистентная копия диска Docker с проверкой SHA-256.
- `scripts/smoke-offline.sh`/`.ps1` — офлайн-проверка 19 файлов тестов без БД, Docker и сети; без локального venv завершается кодом 2.
- Приёмка идёт через обёртку compose, а UI-smoke в ней обязателен (`TP_UI_REQUIRED=1`): недоступный интерфейс валит прогон, а не даёт SKIP.
- `--live-1c` в приёмке больше не требует `--llm`: без ключа план строится `provider=local`, а `--llm` при пустом ключе даёт FAIL с текстом.
- `--live-1c` требует проект с `web_url` из `TP_ACCEPTANCE_PROJECT_ID` (FAIL вместо SKIP); живой прогон пишет в этот проект suite/evidence и сдвигает `last_run`.
- Исправлен выбор навигационного предложения в приёмке: `jget`/`eval` заменён на `python -c`, иначе `INDEX` оставался пустым и живой шаг падал.
- У скриптов приёмки появился `--help`/`-Help` со списком флагов и их независимостью.
- Нормализация переводов строк (LF) для изменённых файлов — отдельными служебными коммитами.

## Инцидент 2026-11-17

`docker compose down -v` на рабочем стенде удалил тома `testpilot_pgdata` (33 проекта, 39 загрузок, 45 suite,
42 записи `code_audit`, 107 evidence) и `testpilot_codex_auth`. Данные **не восстановлены**: дампов не найдено.
Предотвращение: обёртки `scripts/compose.sh`/`compose.ps1` для скриптов репозитория, CI в проекте `testpilot-ci`, бэкап/restore-скрипты,
плейбук разбора копии диска. Подробности — [docs/incident-2026-11-17.md](docs/incident-2026-11-17.md).

Второй случай того же дня: при проверке текста документа команда поиска была собрана с обратными кавычками внутри
двойных кавычек, bash выполнил подстановку и запустил `docker compose down -v` напрямую, минуя обёртку. Удалены пустые
тома, созданные после первого инцидента; потерь данных нет — в БД было 0 проектов и 0 записей `code_audit`, репозиторий
не затронут. Вывод: docker-команды не собирать из текстов документов и логов, поиск — `rg --fixed-strings` в одинарных
кавычках, предохранитель действует только для вызовов через обёртку.
