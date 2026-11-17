# Плейбук: извлечение данных из копии диска Docker

Документ исследовательский, не проверен на реальном диске: все действия только с копией, оригинал не монтировать ни в каком
режиме. Сам ничего не запускает — команды выполняются вручную, **вне приёмки**; успех не гарантирован.

## Предпосылки и правила

- Docker Desktop остановлен, затем выполнен `wsl --shutdown`; копия `docker_data.vhdx` сделана `scripts/forensic-copy.ps1`
  **на другой физический диск**, SHA-256 копии зафиксирован и совпал с источником.
- Временные каталоги — не на диске с оригиналом; записи на копию меняют её SHA-256 — фиксируйте любое изменение.
- Работать **только с копией**: оригинал не монтировать; копию — только `ro` (`noload` для ext4).

## Шаг 1. Подключить копию

Windows (нужны права администратора и остановленный Docker Desktop), WSL2:

```
wsl --mount --vhd D:\forensic\docker_data.vhdx --bare   # именно копия, не оригинал
wsl -d Ubuntu-24.04
sudo lsblk -f                                           # ищем FSTYPE=ext4 у подключённой копии
sudo mkdir -p /mnt/forensic
sudo mount -o ro,noload /dev/<найденное> /mnt/forensic   # раздел или весь диск; noload — опция ext4
```

Если ext4 на LVM (`LVM2_member` в `lsblk -f`): `sudo vgchange -ay` (только включение томов), затем монтировать
`/dev/mapper/<vg>-<lv>` с тем же `ro`. Linux-хост без WSL: `sudo guestmount -a docker_data.vhdx -m /dev/sda1 --ro /mnt/forensic`.

## Шаг 2. Найти данные PostgreSQL

Том `testpilot_pgdata` лежит в каталоге томов Docker внутри файловой системы:

```
sudo find /mnt/forensic -maxdepth 4 -type d -name 'testpilot_pgdata'
sudo ls /mnt/forensic/docker/volumes/testpilot_pgdata/_data
cat /mnt/forensic/docker/volumes/testpilot_pgdata/_data/PG_VERSION
sudo ls /mnt/forensic/docker/volumes/testpilot_pgdata/_data/base
```

Признак живых данных: `PG_VERSION`, `base/`, `global/`, `pg_wal/`, в `base/<oid>/` есть файлы таблиц; нет каталога — шаг 4.

## Шаг 3. Поднять PostgreSQL на копии каталога и снять дамп

PostgreSQL не стартует из каталога, смонтированного только на чтение, поэтому копируем каталог на свободное место:
```
sudo cp -a /mnt/forensic/docker/volumes/testpilot_pgdata/_data ./pgdata-copy   # занимает столько же места
sudo chown -R 999:999 ./pgdata-copy
docker run --rm -d --name pg-forensic -e POSTGRES_PASSWORD=forensic-throwaway -e POSTGRES_DB=testpilot \
  -v "$PWD/pgdata-copy:/var/lib/postgresql/data" -p 127.0.0.1:55432:5432 postgres:17-alpine
docker logs --tail 50 pg-forensic
docker exec pg-forensic pg_dumpall -U testpilot > recovered.sql
docker exec pg-forensic pg_dump -U testpilot -d testpilot -Fc > recovered.dump
docker stop pg-forensic
```

Порт `55432` временный и только на loopback, пароль `forensic-throwaway` одноразовый — в рабочей среде не использовать.

Контейнер работает с копией каталога (`./pgdata-copy`), а не с смонтированной копией диска.
Дальше дамп восстанавливается штатно: `scripts/restore.sh --dump recovered.sql --yes --project testpilot-restore`
(см. `docs/incident-2026-11-17.md`). Это не отменяет правило «только в пустую БД\).

## Шаг 4. Если каталог pgdata отсутствует

Поиск по сигнатурам: `sudo testdisk /mnt/forensic` (просмотр), `sudo photorec /dev/<найденное>` (вывод на другой диск),
`pg_filedump -i -f …` для отдельных 8-КБ страниц (`pg_filedump` — отдельный пакет, например `postgresql-17-pg-filedump`).
`photorec`/`testdisk`/`extundelete` находят лишь фрагменты и целостность БД не гарантируют: собранный кластер может не запуститься.

## Ограничения и чего не делать

- WSL2-диск — виртуальный vhdx, а не сырой ext4; часть данных может быть утеряна при TRIM, успех не гарантирован.
- Не монтировать оригинал и не запускать его в Docker Desktop; не выполнять `initdb`; не включать плейбук в приёмку и CI.
