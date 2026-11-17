# Плейбук: извлечение данных из копии диска Docker

Документ для случая, когда дампа нет, а данные потеряны. Ничего не запускает сам;
команды ниже выполняются вручную, **вне приёмки**. Успех не гарантирован.

## Предпосылки и правила

- Docker Desktop остановлен, затем выполнен `wsl --shutdown` (иначе vhdx занят и копия/монтирование несогласованны).
- Копия `docker_data.vhdx` сделана `scripts/forensic-copy.ps1` **на другой физический диск**; SHA-256 копии зафиксирован и совпал с источником.
- Работать **только с копией**: оригинал не монтировать ни в каком режиме, монтирование копии — только `ro` (`noload` для ext4).
- Временные каталоги — не на диске с оригиналом; записи на копию меняют её SHA-256, фиксируйте это.

## Шаг 1. Подключить копию

Windows (нужны права администратора), WSL2:

```
wsl --mount --vhd D:\forensic\docker_data.vhdx --bare   # именно копия, не оригинал
wsl -d Ubuntu-24.04
sudo lsblk                                              # /dev/sdX — только что подключённая копия
sudo mkdir -p /mnt/forensic
sudo mount -o ro,noload /dev/sdX1 /mnt/forensic          # монтируем копию только для чтения
```

Linux-хост без WSL: `sudo guestmount -a docker_data.vhdx -m /dev/sda1 --ro /mnt/forensic`.

## Шаг 2. Найти данные PostgreSQL

Том `testpilot_pgdata` лежит в каталоге томов Docker внутри файловой системы:

```
sudo find /mnt/forensic -maxdepth 4 -type d -name 'testpilot_pgdata'
sudo ls /mnt/forensic/docker/volumes/testpilot_pgdata/_data
cat /mnt/forensic/docker/volumes/testpilot_pgdata/_data/PG_VERSION
sudo ls /mnt/forensic/docker/volumes/testpilot_pgdata/_data/base
```

Признак живых данных: есть `PG_VERSION`, `base/`, `global/`, `pg_wal/`, и `base/<oid>/` содержит
файлы таблиц. Если каталога тома нет — переходите к шагу 4.

## Шаг 3. Поднять PostgreSQL на копии каталога и снять дамп

PostgreSQL не стартует из каталога, смонтированного только на чтение, поэтому копируем каталог:

```
sudo cp -a /mnt/forensic/docker/volumes/testpilot_pgdata/_data ./pgdata-copy
sudo chown -R 999:999 ./pgdata-copy
docker run --rm -d --name pg-forensic -e POSTGRES_PASSWORD=forensic-throwaway -e POSTGRES_DB=testpilot \
  -v "$PWD/pgdata-copy:/var/lib/postgresql/data" -p 127.0.0.1:55432:5432 postgres:17-alpine
docker logs --tail 50 pg-forensic
docker exec -T pg-forensic pg_dumpall -U testpilot > recovered.sql
docker exec -T pg-forensic pg_dump -U testpilot -d testpilot -Fc > recovered.dump
docker stop pg-forensic
```

Контейнер работает с копией каталога (`./pgdata-copy`), а не с смонтированной копией диска.
Дальше дамп восстанавливается штатно: `scripts/restore.sh --dump recovered.sql --yes --project testpilot-restore`
(см. `docs/incident-2026-11-17.md`). Это не отменяет правило «только в пустую БД\).

## Шаг 4. Если каталог pgdata отсутствует

Ищем по сигнатурам на копии: `sudo testdisk /mnt/forensic` (просмотр), `sudo photorec /dev/sdX1`
(вывод на другой диск), `pg_filedump -i -f …` для отдельных 8-КБ страниц. Ожидания низкие:
в overlay2 удалённые файлы часто недоступны, WSL2-vhdx может обнулять освобождённые блоки.

## Ограничения и чего не делать

- WSL2-диск — виртуальный vhdx, а не сырой ext4; часть данных может быть утеряна при TRIM, успех не гарантирован.
- Не монтировать оригинал и не запускать его в Docker Desktop; не выполнять `initdb` над найденным каталогом.
- Не включать плейбук в приёмку и CI: это ручная процедура на копии.
