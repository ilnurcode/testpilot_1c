# Установщик для Windows (Inno Setup 6)

Вход установщика — каталог staging, собранный по белому списку. Репозиторий
целиком в пакет не попадает.

```powershell
python installer/stage.py                              # staging + манифест
python installer/stage.py --verify installer/staging    # перепроверка состава
ISCC.exe installer\testpilot.iss                       # сборка, вывод в dist_setup\
```

`installer/testpilot.iss` собирает только `staging\*` и не компилируется, если
нет `staging\version.iss` или `staging\manifest.json`.

Собрать, не засоряя каталог репозитория (свой каталог вывода)::

```powershell
ISCC.exe /O"$env:TEMP\tp-setup" installer\testpilot.iss
```

## Установка только для текущего пользователя

Установщик не требует администратора и ничего не пишет в общие места:
`PrivilegesRequired=lowest`, поэтому UAC не появляется, а удаление так же
проходит без повышения прав.

| Что | Где |
| --- | --- |
| Каталог программы | `%LOCALAPPDATA%\Programs\TestPilot1C` (`DefaultDirName`) |
| Ярлыки Start Menu | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\TestPilot 1C\` (`{userprograms}`) |
| Ярлык Рабочего стола | `%USERPROFILE%\Desktop\TestPilot 1C.lnk` (`{userdesktop}`, задача `desktopicon`) |
| Ключ удаления | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E2F5B7C-8F4A-4F1B-9C6D-2B3E7A1D9F42}_is1` |
| Данные, окружение, логи | `%LOCALAPPDATA%\TestPilot1C` (`first-run.ps1 -Packaged`), вне каталога программы |

Ничего из этого не пересекается с `HKLM`, `ProgramData` и `C:\Users\Public`.
`AppId` постоянный, поэтому повторный запуск установщика обновляет ту же
установку на месте (`UsePreviousAppDir=yes`), а не добавляет вторую запись и
второй комплект ярлыков.

Молчаливые команды (без администратора, подтверждено на этой машине):

```powershell
# установка
Start-Process -Wait -FilePath "TestPilot_Setup.exe" `
  -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'   # exit 0

# удаление
Start-Process -Wait -FilePath "$env:LOCALAPPDATA\Programs\TestPilot1C\unins000.exe" `
  -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'   # exit 0
```

Почему прежняя схема ломалась (наблюдалось на этой машине): при
административной установке (по умолчанию `PrivilegesRequired=admin`) Inno
пишет ключ удаления в `HKLM`, ярлыки в `ProgramData\...\Start Menu` и на общий
Рабочий стол. Удалятор такой установки требует тех же прав, что и установка:
запуск `unins000.exe /VERYSILENT` без подтверждённого повышения завершился
кодом `1` («сбой инициализации»), `is-*.tmp` от него остался в `%TEMP%`, а
ключ `HKLM` и общие ярлыки без администратора не удаляются — остатки остаются
в системе. Аргумент `/ ?` не является справкой: удалятор его игнорирует и
показывает модальное подтверждение, поэтому в скрипте это выглядит зависанием.

Следы прежней административной установки новый установщик не переносит и не
удаляет сам: он ставится рядом, а свою запись помечает «(Current user)», чтобы
две записи не выглядели дублем. Один раз убрать остатки можно только с правами
администратора:

```powershell
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6E2F5B7C-8F4A-4F1B-9C6D-2B3E7A1D9F42}_is1" /f
Remove-Item -Recurse -Force "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\TestPilot 1C"
Remove-Item -Force "C:\Users\Public\Desktop\TestPilot 1C.lnk"
```

## Состав поставки

Список задан в `installer/stage.py`:

- запуск: `launcher.py`, `TestPilot.cmd`, `Run-TestPilot.bat`,
  `Stop-TestPilot.bat`, `Запустить_в_Docker.cmd`, `Остановить TestPilot.cmd`,
  `Остановить_в_Docker.cmd`;
- пакет `testpilot/**` (`.py`, `.js`, `.css`, `.html`), включая
  `testpilot/web/static`;
- `migrations/**` (`.py`, `.mako`, `.ini`) и `alembic.ini`;
- скрипты запуска и обслуживания `scripts/**` (`.ps1`, `.py`), кроме
  `EXCLUDED_SCRIPTS`;
- `pyproject.toml`, `requirements.lock`, `compose.yaml`, `Dockerfile`,
  `.dockerignore`, `.env.example`.

Не попадают никогда (нет в списке, плюс вторая проверка запретных путей):
`.env`, `.runtime`, `.venv`, `.git`, `.github`, `.wheelhouse`, `.pytest_cache`,
`__pycache__`, `dist`, `dist_setup`, `build`, `testpilot_1c.egg-info`, `samples`,
`tests`, `contracts`, `docs`, `installer`.

`scripts/acceptance.ps1`, `scripts/smoke-offline.ps1`, проверочные `scripts/*_smoke.py`
(`a2_smoke.py`, `isolation_smoke.py`), браузерные smoke-скрипты, `benchmark.py` и
`export_contracts.py` исключены осознанно: им нужны `tests/`,
`samples/` или `contracts/`, которых в пакете нет, а запуск приложения они не
обслуживают. Проверено: цепочка `TestPilot.cmd → launcher.py →
scripts/start.ps1` использует только поставляемые файлы; `.ps1`-скрипты пакета
не вызывают `.sh`.

`contracts/` — выход `scripts/export_contracts.py` (инструмент разработки), в
рантайме не читается. `samples/` — данные демонстраций и тестов, рантайм их не
открывает (в UI остаётся лишь пример пути для аудита).

Установленный `.env` и `.runtime` предыдущей установки не перезаписываются и не
удаляются: их нет в пакете, а обновление ставится в тот же каталог.

## Версия

Один источник — `[project] version` в `pyproject.toml`. Упаковщик пишет
`staging/version.iss`, установщик использует `AppVersion={#AppVersion}`.
Вручную заданная версия в установщике не поддерживается.

## Запускной launcher и его подтверждение

Время файла ничего не доказывает, поэтому соответствие `TestPilot.exe`
исходнику проверяется только отчётом сборки `installer/launcher-build.json`:
SHA-256 и размер exe, SHA-256 `launcher.py`, инструмент сборки и его версия.

- отчёта нет или он не совпадает → exe в пакет не включается, штатный запуск
  идёт через `TestPilot.cmd` и `launcher.py`; в манифесте остаётся gate;
- `--release` без подтверждённого отчёта → отказ `unverified_launcher`;
- подтверждённый exe попадает в пакет, но `--verify` всё равно сверяет его
  SHA-256 со staging и падает при подмене.

Рецепт сборки и отчёт:

```powershell
# 1. Инструмент ставит владелец вручную (скрипты упаковки этого не делают)
.venv\Scripts\python.exe -m pip install "pyinstaller==6.11.0"

# 2. Сборка строго по рецепту и отчёт по фактическим файлам
.venv\Scripts\python.exe installer\launcher_receipt.py --build

# 3. Проверка отчёта: расхождение версий/подмена exe видна сразу
.venv\Scripts\python.exe installer\launcher_receipt.py --check
```

`installer/launcher.spec` — единственный рецепт сборки launcher.
`installer/launcher_receipt.py --write` пишет отчёт по уже собранному exe и
отказывается работать без установленного PyInstaller: версию инструмента нельзя
подставить вручную, иначе отчёт ничего не подтверждает. `--check` сообщает о
подмене exe, изменении `launcher.py` или расхождении размера.

Здесь этот запуск не выполнялся: PyInstaller в окружении нет, поэтому наличие
рецепта не равно проверенной сборке. Отчёт не создаётся «на будущее» — без него
exe в пакет не попадает.

## Безопасность каталога staging

- staging собирается в новом уникальном каталоге рядом с целевым и подменяет
  только результат, помеченный этим упаковщиком (`generated_by`);
- непустой каталог без маркера упаковщика не трогается — отказ `dest_not_owned`;
- запрещены: `dest` = корень репозитория, `dest` выше корня, любой каталог
  внутри репозитория кроме `installer/staging`, каталог внутри деревьев
  поставки;
- символьные ссылки и reparse-точки не копируются; файлы вне корня репозитория
  не копируются.

## Что проверяет `--verify`

`missing_manifest`, `dest_not_owned`, `version_mismatch`, `missing_files`,
`unexpected_files`, `digest_mismatch`, `launcher_mismatch`,
`unverified_launcher`, `version_file`, `source_drift` (исходники изменились
после staging — пересоберите staging и только потом компилируйте установщик).

## Точки входа (единый путь запуска)

Владелец startup/init — A3 (`scripts/first-run.ps1`, `init.ps1`, `install.ps1`,
`start.ps1`, `stop.ps1`). Обёртки пакета только делегируют и не запускают
сервис сами:

- `TestPilot.cmd`, `Run-TestPilot.bat` → `powershell -File scripts\first-run.ps1`
  (`-Packaged` автоматически, когда в корне пакета есть `manifest.json`, то есть
  для установленного пакета). Своего поиска `.venv` нет: в пакетном режиме
  окружение и данные живут в `TP_DATA_DIR` (по умолчанию `%LOCALAPPDATA%\TestPilot1C`).
- `Запустить_в_Docker.cmd` → `Run-TestPilot.bat` (никакого отдельного
  `docker compose up` с обещанием «успешно запущен»).
- `Остановить_в_Docker.cmd` → `Stop-TestPilot.bat`; `Stop-TestPilot.bat` →
  `scripts\stop.ps1` (владельцем сервисов), с резервным `docker compose down`
  только если скрипта нет.
- Инициализация схемы — только `scripts\init.ps1`; обновление существующей
  схемы — `scripts\update.ps1 -Confirmed`. `first-run.ps1` не мигрирует
  существующую БД и отказывает при устаревшей схеме.
- `[Icons]`/`[Run]` установщика указывают на `first-run.ps1 -Packaged`
  (правка A3/Main); автостарт без проверки готовности не возвращается.

## C2: согласование точки входа с A3

Inno `[Run]` после установки указывает на `first-run.ps1 -Packaged` с флагами
`postinstall skipifsilent nowait`: окружение создаёт этот скрипт, установщик
его не создаёт, а молчаливая установка ничего не запускает и потому не выдаёт
себя за готовый сервис.

Что нужно согласовать с A3 (владелец `scripts/start*.ps1`, `install.ps1`,
`windows-common.ps1`, `local_env.py`, `launcher.py`, `compose.yaml`):

1. Единый вход после установки: `scripts\install.ps1` создаёт `.venv`/`.env`
   (при необходимости — в каталоге данных вне Program Files), затем startup
   проверяет готовность.
2. Имя и параметры этого входа для `[Run]` иконки и ярлыка: либо
   `scripts\install.ps1` в режиме «первый запуск», либо отдельный
   `scripts\first-run.ps1`.
3. Каталог данных: `TP_RUNTIME_DIR`/`TP_ENV_FILE` (контракт в разделе выше).
4. После этого `[Run]` возвращается в `installer/testpilot.iss`. Правку `[Run]`
   и ярлыков может внести владелец A3 или основной исполнитель — файл открыт для
   этого изменения; условие одно: реальная установка на чистую Windows и
   успешный запуск, иначе оставляем без автозапуска.

## Оставшиеся gates выпуска

1. C2 PENDING: сборка ISCC, установка, обновление поверх и удаление без
   администратора проверены на машине разработчика (exit 0, 100/100 файлов
   совпали по sha256); установка и запуск на чистой Windows x64 остаются
   непроверенными, как и обновление между разными версиями. `preflight.py` не
   заменяет эту проверку и не является установкой.
2. `dist_setup\` не игнорируется корневым `.gitignore` — файл общий, его ведёт
   основной исполнитель.

## Чистая установка из исходников (без установщика)

```powershell
# 1. Проверка: ничего не устанавливает, не копирует и не меняет
powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1

# 2. Новый пустой каталог данных вне Program Files (существующий .env не переносится)
powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1 -Prepare -DataDir "$env:LOCALAPPDATA\TestPilot1C"

# 3. Установка зависимостей и запуск — вручную, по подсказкам проверки
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

`bootstrap.ps1` только находит Python 3.12 x64 и вызывает `installer/preflight.py`.
Он ничего не скачивает и не ставит: ни Python, ни Docker, ни пакеты.

### Границы ответственности

`preflight.py` проверяет только **предпосылки** установки. Готовность сервиса —
единственный механизм startup (`scripts/start.ps1` и его health-check), владелец
A3. Проверка файлов не выдаётся за «сервис готов»: при собранных предпосылках
печатается «ПРЕДУСЛОВИЯ СОБРАНЫ» и ссылка на startup, а `.runtime` — состояние
startup, а не предпосылка, и вердикт не блокирует.

### Что проверяет preflight

Python 3.12 x64, PowerShell 5.1+, запущенный Docker Desktop в режиме
Linux-контейнеров, свободные порты 8000/8001/8002, полноту файлов пакета
(`launcher.py`, `pyproject.toml`, `scripts/install.ps1`, `scripts/start.ps1`),
расположение приложения вне Program Files, запись в каталог, наличие
`.venv\Scripts\python.exe`, `.env` и `.runtime`, а также целевой каталог данных.

Вердикт «ПРЕДУСЛОВИЯ СОБРАНЫ» требует `.venv`, `.env` и всех обязательных
пунктов. Отсутствие любого — «ПРЕДУСЛОВИЯ НЕ СОБРАНЫ» с перечнем и конкретной
командой. `.runtime` показывается справочно и вердикт не меняет. `--no-probes` отключает проверки окружения (Docker,
PowerShell, порты) и в этом режиме предпосылки принципиально не подтверждаются:
пропущенная проверка никогда не считается пройденной.

`-Prepare` создаёт новый пустой каталог данных. Существующий `.env` не
переносится, не копируется и не изменяется: при его наличии в целевом каталоге
проверка отказывает и просит новый путь. Внутри Program Files целевой каталог
запрещён, потому что `.env` и `.runtime` там недоступны для записи.

### Контракт для скриптов (владелец — основной исполнитель A3)

Сейчас `scripts/windows-common.ps1` жёстко берёт `.runtime` и `.env` из корня
пакета, поэтому при установке в Program Files runtime-данные оказываются там же.
Минимальное изменение, снимающее это ограничение:

1. `$script:RuntimeDir = if ($env:TP_RUNTIME_DIR) { $env:TP_RUNTIME_DIR } else { Join-Path $script:ProjectRoot '.runtime' }`
2. `$path = if ($env:TP_ENV_FILE) { $env:TP_ENV_FILE } else { Join-Path $script:ProjectRoot '.env' }`
3. `scripts/local_env.py` принимает `--output` (по умолчанию `.env`) и никогда не перезаписывает существующий файл.
4. `scripts/start.ps1` перед запуском вызывает `installer/preflight.py` и при коде 2 печатает его отчёт вместо «Ready».
5. `launcher.py` при недоступном `/health` не сообщает о готовности, а печатает путь к `installer/preflight.py`.

Пока этого нет, пакет, положенный в Program Files вручную, не может считаться
готовым: проверка честно показывает «НЕ ГОТОВО» по расположению и runtime.
Сам установщик в Program Files не ставит — его каталог
`%LOCALAPPDATA%\Programs\TestPilot1C` доступен пользователю на запись.
