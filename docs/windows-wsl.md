# Установка и запуск на Windows

Основной способ — Windows 11 или Windows 10 версии 2004+ (сборка 19041+) с WSL2, Ubuntu 24.04 и Docker Desktop. Интерфейс открывается обычным браузером Windows; Python, Playwright и процессы сервиса работают внутри Ubuntu. Нативный запуск через PowerShell не поддерживается текущими bash-скриптами.

Понадобятся интернет для первой установки, включённая аппаратная виртуализация и свободное место для Ubuntu, Docker и браузера. На рабочем ПК установка WSL/Docker может требовать согласования с администратором. Ниже указано, в каком терминале выполнять команды. Не копируйте Linux-команды в PowerShell.

## 1. Установите WSL2 и Ubuntu

Откройте **PowerShell от имени администратора**:

```powershell
wsl --install -d Ubuntu-24.04
```

Перезагрузите Windows, если это предложит установщик. Откройте **Ubuntu 24.04** из меню «Пуск» и создайте Linux-пользователя и пароль. При вводе Linux-пароля символы не отображаются — это нормально. Этот пароль потребуется для sudo и отличается от пароля входа в TestPilot.

В PowerShell проверьте:

```powershell
wsl --update
wsl --list --verbose
```

У Ubuntu-24.04 должна быть VERSION 2. Если стоит 1:

```powershell
wsl --set-version Ubuntu-24.04 2
```

Официальная инструкция: https://learn.microsoft.com/windows/wsl/install

## 2. Установите Docker Desktop

Установите Docker Desktop для Windows с официального сайта: https://docs.docker.com/desktop/setup/install/windows-install/

Запустите Docker Desktop и дождитесь готовности движка. Используйте Linux containers. В настройках включите:

- **General → Use the WSL 2 based engine**;
- **Resources → WSL Integration → Ubuntu-24.04**, затем Apply & restart.

Названия пунктов могут немного отличаться между версиями Docker Desktop. Отдельный Docker Engine через apt внутри Ubuntu устанавливать не нужно.

Далее все команды, кроме явно подписанных PowerShell, выполняйте в **Ubuntu 24.04**:

```bash
docker version
docker compose version
```

Первая команда должна показывать Client и Server, вторая — Compose v2. Если Server недоступен, проверьте, что Desktop запущен и интеграция Ubuntu включена.

## 3. Скачайте проект и установите зависимости

В Ubuntu:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates python3.12 python3.12-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

mkdir -p ~/projects
cd ~/projects
git clone https://github.com/ilnurcode/testpilot_1c.git
cd testpilot_1c

uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python scripts/local_env.py
```

Команда curl запускает официальный установщик uv; его описание: https://docs.astral.sh/uv/getting-started/installation/

Если репозиторий приватный, войдите в GitHub разрешённым способом на своём компьютере. Пароли и токены не добавляйте в URL, .env.example или коммиты. Если папка проекта уже существует, не клонируйте поверх неё — используйте существующий checkout и обновление из раздела ниже.

Храните проект в Linux-папке `~/projects`, а не в `/mnt/c/...`: так меньше проблем с правами, скоростью файловой системы и окончаниями строк. Linux `.venv` нельзя заменять виртуальным окружением Windows.

## 4. Установите Chromium для Playwright

В Ubuntu:

```bash
.venv/bin/python -m playwright install --with-deps chromium

TP_BROWSER_PATH="$(.venv/bin/python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()')"
printf '\nTP_CHROMIUM_PATH=%s\n' "$TP_BROWSER_PATH" >> .env
unset TP_BROWSER_PATH
```

Playwright скачает совместимый Linux-браузер и установит системные зависимости; может потребоваться sudo-пароль Ubuntu. Наличие Chrome/Edge в Windows этого шага не заменяет. Путь сохраняется локально в .env; сам файл исключён из Git. После обновления Playwright повторите этот шаг, если изменился путь браузера.

## 5. Запустите сервис

В Ubuntu, из `~/projects/testpilot_1c`:

```bash
export DOCKER_CONFIG="$HOME/.docker"
./scripts/start-presentation.sh
```

Переменная DOCKER_CONFIG позволяет использовать конфигурацию Docker Desktop вашего пользователя. Она нужна и в новой сессии Ubuntu перед повторным запуском. Скрипт поднимает PostgreSQL, применяет миграции, проверяет Chromium и запускает приложения. При первой загрузке Docker-образа нужно подождать.

Дождитесь сообщений:

```text
Health всех трёх сервисов: ok
Презентационный режим готов.
```

В **браузере Windows** откройте `http://localhost:8000`. Если имя localhost не сработало, попробуйте `http://127.0.0.1:8000`. Перенаправление localhost между Windows и WSL2 обычно включено по умолчанию.

Вход: `expert`. Чтобы узнать только локальный пароль этой роли, выполните в приватном терминале Ubuntu:

```bash
sed -n 's/^TP_EXPERT_PASSWORD=//p' .env
```

Эта команда выводит пароль. Не выполняйте её при демонстрации экрана, не отправляйте вывод в чат и не включайте в скриншоты. Другие роли: analyst и runner с отдельными паролями в .env. Не копируйте пустой .env.example поверх созданного .env.

Сохраните .env: в нём также пароль уже созданной БД. Удаление .env не сбрасывает пароль существующего PostgreSQL. Оригиналы файлов, планы и отчёты хранятся в Docker volume `testpilot_pgdata`; исходная папка проекта сама по себе не является резервной копией БД.

## 6. Проверьте и покажите демонстрацию

В браузере:

1. **Обзор → Открыть демонстрацию**.
2. Покажите историю двух версий, 5 изменений и 10 затронутых объектов.
3. **Подготовить проверки → Учебные правила — без нейросети → Сформировать сценарии**.
4. Просмотрите сценарии и данные, введите причину подтверждения, подтвердите оба сценария.
5. **Запустить на учебном стенде**.
6. Покажите 2 пройденные проверки и 2 расхождения, ожидаемое/фактическое значение, скриншоты и историю дефектов.
7. Обновите страницу — отчёт сохранится.

Расхождения здесь намеренные: учебный стенд теряет скидку при количестве от 10. Это проверка работающего синтетического приложения, а не настоящей 1С. API-ключ для такого показа не требуется. Подробный сценарий: [presentation.md](presentation.md).

Необязательная автоматическая проверка из Ubuntu:

```bash
set -a
. ./.env
set +a
.venv/bin/python scripts/smoke.py --health-only
.venv/bin/python scripts/presentation-smoke.py
```

Вторая команда smoke создаёт отдельный учебный анализ и выполняет реальные HTTP/Playwright-проверки. Без sudo. Успешный результат содержит passed=2, failed=2, error=0 и screenshots=2.

## Повторный запуск и остановка

После перезагрузки сначала запустите Docker Desktop, затем Ubuntu:

```bash
cd ~/projects/testpilot_1c
export DOCKER_CONFIG="$HOME/.docker"
./scripts/start-presentation.sh
```

Для полной остановки WSL-процессов закройте активную работу в других WSL-дистрибутивах и выполните в **PowerShell**:

```powershell
wsl --shutdown
```

Эта команда останавливает все дистрибутивы WSL, включая используемые Docker Desktop. При необходимости завершите Docker Desktop через его меню. Данные volume при штатной остановке сохраняются. Не используйте `docker compose down -v`, Docker factory reset или удаление Ubuntu для обычной остановки — они могут удалить данные.

## Обновление проекта

Если локальных изменений исходников нет, в Ubuntu:

```bash
cd ~/projects/testpilot_1c
git pull --ff-only
export DOCKER_CONFIG="$HOME/.docker"
./scripts/start-presentation.sh
```

Если Git сообщает о локальных изменениях, сначала сохраните их; не выполняйте reset --hard ради обновления. При изменении версии Playwright повторите установку браузера из шага 4, затем запуск. Локальный .env Git не заменяет.

## Настройка внешней нейросети — необязательно

Откройте локальный .env командой `nano .env` и добавьте настройки своего OpenAI-совместимого провайдера:

```dotenv
TP_LLM_BASE_URL=https://api.openai.com/v1
TP_LLM_MODEL=gpt-4.1-mini
TP_LLM_API_KEY=ВАШ_КЛЮЧ
```

Замените обозначение ключа реальным значением только у себя. Сохраните файл и снова выполните start-presentation.sh. Ключ не публикуется в Git. API-провайдер может требовать оплату; подписка ChatGPT сама по себе не даёт API-доступ.

В интерфейсе выберите API и подтвердите передачу контекста провайдеру. Наличие ключа не гарантирует доступность модели. Фактический вызов с реальным ключом в облачной проверке не выполнялся.

## Если что-то не запускается

| Симптом | Что проверить |
|---|---|
| WSL сообщает о виртуализации | Включите виртуализацию в UEFI/BIOS и компоненты WSL/Virtual Machine Platform; следуйте диагностике Microsoft. |
| docker не найден или нет Server | Запустите Docker Desktop, включите интеграцию именно Ubuntu-24.04, откройте новый терминал Ubuntu. |
| Ошибка credential helper | Задайте `export DOCKER_CONFIG="$HOME/.docker"`; проверьте работоспособность Docker Desktop в Ubuntu. Не удаляйте учётные данные наугад. |
| Chromium не найден | Повторите шаг 4 в том же Ubuntu-пользователе и проверьте TP_CHROMIUM_PATH в .env. |
| Address already in use | Порты 5432, 8000, 8001, 8002 должны быть свободны. Определите конфликтующее приложение; не завершайте чужие процессы наугад. |
| Страница недоступна | Дождитесь health, убедитесь, что Docker/Ubuntu не остановлены. Проверьте `curl -f http://127.0.0.1:8000/health` внутри Ubuntu. |
| Health в Ubuntu работает, Windows не открывает страницу | Проверьте localhost forwarding WSL и локальные правила VPN/брандмауэра; не открывайте сервис в интернет для обхода проблемы. |
| password authentication failed после удаления .env | Восстановите исходный .env из своей защищённой копии: новый случайный пароль не совпадает с сохранённой БД. |
| `bash\r` или Permission denied у .sh | Клонируйте репозиторий Git внутри Ubuntu, как в шаге 3. В репозитории зафиксированы LF-окончания и исполняемые права скриптов. |

Логи приложений: `.runtime/impact.log`, `.runtime/intelligence.log`, `.runtime/web.log`. Логи БД: `docker compose logs --tail=100 db`. Перед пересылкой любых логов проверьте отсутствие паролей и персональных данных.

## Что фактически проверено

В облачном Linux проверены установка, повторный запуск, 82 pytest с PostgreSQL, сквозные HTTP/браузерные сценарии и сохранность данных после перезапуска. Установка на реальном Windows/WSL2 в этой среде не выполнялась: инструкция использует тот же Linux-путь запуска и отдельно описывает подготовку Windows. Живая 1С/OData и автоматический разбор бинарных выгрузок пока не подключены.
